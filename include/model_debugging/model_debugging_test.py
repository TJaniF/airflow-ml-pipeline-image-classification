from airflow.models.baseoperator import BaseOperator
import torch
from torch.utils.data import DataLoader
from transformers import ResNetForImageClassification
import os
from torch.utils.data import Dataset
from PIL import Image
from torchvision import transforms
import numpy as np

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def calculate_binary_classification_metrics(y_true, y_pred, threshold=0.5):
    """
    Calculate evaluation metrics for binary classification.

    :param y_true: List or array of true binary labels.
    :param y_pred: List or array of predicted binary labels.
    :param threshold: Threshold value to use for binarizing predictions (default=0.5).

    Returns: Dictionary containing calculated metrics (precision, recall, accuracy, F1-score, false positives,
    false negatives, true positives, true negatives, and AUC).
    """
    y_pred_binary = (np.array(y_pred) >= threshold).astype(int)

    precision = precision_score(y_true, y_pred_binary)
    recall = recall_score(y_true, y_pred_binary)
    accuracy = accuracy_score(y_true, y_pred_binary)
    f1 = f1_score(y_true, y_pred_binary)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred_binary).ravel()
    auc = roc_auc_score(y_true, y_pred)

    return {
        "precision": precision,
        "recall": recall,
        "accuracy": accuracy,
        "f1_score": f1,
        "false_positives": fp,
        "false_negatives": fn,
        "true_positives": tp,
        "true_negatives": tn,
        "auc": auc,
    }


def transform_function(image):
    if image.mode == "RGB":
        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )
    elif image.mode == "L":
        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.Grayscale(num_output_channels=3),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )
    else:
        raise ValueError("Unsupported image mode: %s" % image.mode)

    return transform(image)


def test_transform_function(image):
    if image.mode == "RGB":
        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )
    elif image.mode == "L":
        transform = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.Grayscale(num_output_channels=3),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )
    else:
        raise ValueError("Unsupported image mode: %s" % image.mode)

    return transform(image)


class CustomImageDataset(Dataset):
    def __init__(
        self,
        images_paths,
        labels,
        transform_function,
    ):
        self.images_paths = images_paths
        self.labels = labels
        self.transform_function = transform_function

    def __len__(self):
        return len(self.images_paths)

    def __getitem__(self, idx):
        image_path = self.images_paths[idx]
        label = self.labels[idx]

        # print("Image path: " + image_path, "label: " + str(label))

        image = Image.open(image_path)

        if self.transform_function:
            image = self.transform_function(image)

        return image, torch.tensor(label)


class TestHuggingFaceBinaryImageClassifierOperator(BaseOperator):
    """
    Tests a binary HuggingFace image classification model on a list of locally saved images.

    :param model_name: name of the model to use as a string. Can reference a public HuggingFace model
    or be the path to a locally saved model.
    :param criterion: loss function.
    :param local_images_filepaths: list of paths to the testing images (list of str).
    :param labels: list of labels for the testing set (list of floats or ints).
    :param train_transform_function: transform function for training images.
    :param batch_size: size of each training batch.
    :param shuffle: whether or not batches should be shuffled.
    :param num_workers_data_loader: number of workers to be used by the data loader.
    :param ignore_mismatched_sizes_resnet: whether mismatches with the size of the resnet should be ignored.
    """

    ui_color = "#ebab34"

    template_fields = (
        "model_name",
        "criterion",
        "local_images_filepaths",
        "labels",
        "test_transform_function",
        "batch_size",
        "shuffle",
        "num_workers_data_loader",
        "ignore_mismatched_sizes_resnet",
    )

    def __init__(
        self,
        model_name: str,
        criterion,
        local_images_filepaths: list,
        labels: list,
        test_transform_function: callable = None,
        batch_size: int = 32,
        shuffle: bool = False,
        num_workers_data_loader: int = 0,
        ignore_mismatched_sizes_resnet: bool = True,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.model_name = model_name
        self.criterion = criterion
        self.local_images_filepaths = local_images_filepaths
        self.labels = labels
        self.test_transform_function = test_transform_function
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.num_workers_data_loader = num_workers_data_loader
        self.ignore_mismatched_sizes_resnet = ignore_mismatched_sizes_resnet
        self.num_classes = 1

    def execute(self, context=None):
        # loading the test set from list of image paths and list of labels
        test_dataset = CustomImageDataset(
            images_paths=self.local_images_filepaths,
            labels=self.labels,
            transform_function=self.test_transform_function,
        )

        print(f"Successfully created the Test Dataset! Length: {len(test_dataset)}")

        # create the train loader

        test_loader = DataLoader(
            test_dataset,
            batch_size=self.batch_size,
            shuffle=self.shuffle,
            num_workers=self.num_workers_data_loader,
        )

        print(f"Successfully created the Test DataLoader!")

        # fetch model
        model = ResNetForImageClassification.from_pretrained(
            self.model_name, ignore_mismatched_sizes=self.ignore_mismatched_sizes_resnet
        )

        print(f"Fetch model: {self.model_name}")

        model.classifier[-1] = torch.nn.Linear(
            model.classifier[-1].in_features, self.num_classes
        )

        print(f"Model target set to {self.num_classes} classes.")

        # set device
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = model.to(device)

        # start model evaluation
        model.eval()

        test_loss = 0
        predictions = []
        true_labels = []

        print("Starting model evaluation...")

        with torch.no_grad():
            for images, labels in test_loader:
                images = images.to(device)
                labels = labels.to(device)
                labels = labels.unsqueeze(1).float()

                outputs = model(images)
                probabilities = torch.sigmoid(outputs.logits)

                predictions += (probabilities > 0.5).float().cpu().numpy().tolist()
                true_labels += labels.cpu().numpy().tolist()
                

        average_test_loss = test_loss / len(test_loader)
        labels = np.array(self.labels)  
        metrics = calculate_binary_classification_metrics(true_labels, predictions)
        print(predictions)
        print(true_labels)
        print(f"Test Loss: {average_test_loss:.4f}, Metrics: {metrics}")


### SET YOUR PARAMETERS HERE ###

files = ["test_toy/" + x for x in os.listdir("test_toy/")]
labels = [
    0 if file_name.split("/")[-1].split(" ")[0] == "meningioma" else 1
    for file_name in files
]

test_classifier = TestHuggingFaceBinaryImageClassifierOperator(
    task_id="test_classifier",
    model_name="test_trainer/model_schwannoma",
    criterion=torch.nn.BCELoss(),
    local_images_filepaths=files,
    labels=labels,
    test_transform_function=transform_function,
    batch_size=500,
    shuffle=False,
)

test_classifier.execute()
