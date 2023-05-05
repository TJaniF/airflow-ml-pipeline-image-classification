"""
### TITLE

DESCRIPTION
"""

from airflow import Dataset as AirflowDataset
from airflow.decorators import dag, task
from astro.sql import get_value_list
from airflow.operators.empty import EmptyOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
import pendulum
import os
import logging
import requests
import numpy as np
from PIL import Image
import duckdb
import json
import pickle
import shutil
import torch

from include.custom_operators.hugging_face import (
    TestHuggingFaceImageClassifierOperator,
    transform_function,
)

from airflow.providers.slack.notifications.slack_notifier import SlackNotifier

SLACK_CONNECTION_ID = "slack_default"
SLACK_CHANNEL = "alerts"
SLACK_MESSAGE = """
**Model Test Successful** :tada:

The {{ ti.task_id }} task finished testing the model: {{ ti.xcom_pull(task_ids='get_latest_fine_tuned_modelpath') }}!

Test-result:
Average test loss: {{ ti.xcom_pull(task_ids='test_classifier')['average_test_loss'] }}
Test accuracy: {{ ti.xcom_pull(task_ids='test_classifier')['test_accuracy'] }}

Comparison:
Baseline accuracy of the test set: {{ var.value.get('baseline_accuracy', 'Not defined') }}
Pre-fine-tuning average test loss: {{ var.value.get('baseline_model_av_loss', 'Not defined') }}
Pre-fine-tuning test accuracy:  {{ var.value.get('baseline_model_accuracy', 'Not defined') }}
"""

task_logger = logging.getLogger("airflow.task")

TRAIN_FILEPATH = "include/train"
TEST_FILEPATH = "include/test"
FILESYSTEM_CONN_ID = "local_file_default"
DB_CONN_ID = "duckdb_default"
REPORT_TABLE_NAME = "reporting_table"
TEST_TABLE_NAME = "test_table"

S3_BUCKET_NAME = "myexamplebucketone"
S3_IN_FOLDER_NAME = "in_train_data"
S3_TRAIN_FOLDER_NAME = "train_data"
AWS_CONN_ID = "aws_default"
IMAGE_FORMAT = ".jpeg"
TEST_DATA_TABLE_NAME = "test_data"
DUCKDB_PATH = "include/duckdb_database"
DUCKDB_POOL_NAME = "duckdb_pool"

LABEL_TO_INT_MAP = {"glioma": 0, "meningioma": 1}
LOCAL_TEMP_TEST_FOLDER = "include/test"
RESULTS_TABLE_NAME = "model_results"


@dag(
    start_date=pendulum.datetime(2023, 1, 1),
    schedule=[AirflowDataset("new_model_trained")],
    catchup=False,
)
def test_fine_tuned_model():
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    get_image_s3_keys_from_duckdb = get_value_list(
        task_id="get_image_s3_keys_from_duckdb",
        sql=f"SELECT image_s3_key FROM {TEST_DATA_TABLE_NAME};",
        conn_id=DB_CONN_ID,
        pool=DUCKDB_POOL_NAME,
    )

    get_labels_from_duckdb = get_value_list(
        task_id="get_labels_from_duckdb",
        sql=f"SELECT label FROM {TEST_DATA_TABLE_NAME};",
        conn_id=DB_CONN_ID,
        pool=DUCKDB_POOL_NAME,
    )

    @task
    def load_test_images(keys):
        hook = S3Hook(aws_conn_id=AWS_CONN_ID)
        images = []
        for key in keys:
            print(key)
            image = hook.download_file(
                key=key,
                preserve_file_name=True,
                local_path=LOCAL_TEMP_TEST_FOLDER,
                use_autogenerated_subdir=False,
            )
            images.append(image)

        return images

    local_images_filepaths = load_test_images(
        get_image_s3_keys_from_duckdb.map(lambda x: x[0])
    )

    @task
    def get_latest_fine_tuned_modelpath():
        models_dir = "include/pretrained_models"
        models = []
        for dir in os.listdir(models_dir):
            models.append({"folder_name": dir, "timestamp": pendulum.parse(dir)})
        if not models:
            return None
        return (
            "include/pretrained_models/"
            + sorted(models, key=lambda m: m["timestamp"], reverse=True)[0][
                "folder_name"
            ]
            + "/"
        )

    test_classifier = TestHuggingFaceImageClassifierOperator(
        task_id="test_classifier",
        model_name=get_latest_fine_tuned_modelpath(),
        criterion=torch.nn.CrossEntropyLoss(),
        local_images_filepaths=local_images_filepaths,
        labels=get_labels_from_duckdb.map(lambda x: x[0]),
        num_classes=2,
        test_transform_function=transform_function,
        batch_size=32,
        shuffle=False,
        on_success_callback=SlackNotifier(
            slack_conn_id=SLACK_CONNECTION_ID,
            text=SLACK_MESSAGE,
            channel=SLACK_CHANNEL,
        ),
        outlets=[AirflowDataset("new_model_tested")],
    )

    @task
    def delete_local_test_files(folder_path):
        shutil.rmtree(folder_path)

    @task(pool=DUCKDB_POOL_NAME)
    def write_model_results_to_duckdb(db_path, table_name, **context):
        timestamp = context["ti"].xcom_pull(task_ids="test_classifier")["timestamp"]
        test_av_loss = context["ti"].xcom_pull(task_ids="test_classifier")[
            "average_test_loss"
        ]
        test_accuracy = context["ti"].xcom_pull(task_ids="test_classifier")[
            "test_accuracy"
        ]
        model_name = context["ti"].xcom_pull(task_ids="test_classifier")["model_name"]

        con = duckdb.connect(db_path)

        con.execute(
            f"""CREATE TABLE IF NOT EXISTS {table_name} 
            (model_name TEXT PRIMARY KEY, timestamp DATETIME, test_av_loss FLOAT, test_accuracy FLOAT)"""
        )

        con.execute(
            f"INSERT OR REPLACE INTO {table_name} (model_name, timestamp, test_av_loss, test_accuracy) VALUES (?, ?, ?, ?) ",
            (model_name, timestamp, test_av_loss, test_accuracy),
        )

        con.close()

    (
        start
        >> [
            local_images_filepaths,
            get_labels_from_duckdb,
            get_image_s3_keys_from_duckdb,
        ]
    )

    (
        test_classifier
        >> [
            delete_local_test_files(LOCAL_TEMP_TEST_FOLDER),
            write_model_results_to_duckdb(
                db_path=DUCKDB_PATH,
                table_name=RESULTS_TABLE_NAME,
            ),
        ]
        >> end
    )


test_fine_tuned_model()