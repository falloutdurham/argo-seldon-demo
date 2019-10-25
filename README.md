# Argo & Seldon Core Demo

## Introduction

This is a quick tutorial showing how to train and deploy a PyTorch model using Argo and Seldon Core. We'll be serving a simple [GPT-2](https://openai.com/blog/better-language-models/) text generator with trained parameters downloaded from a previous Argo workflow step. 

## Creating the Image with s2i

First, install the [s2i](https://github.com/openshift/source-to-image) image builder utility (assuming MacOS here, for reasons):

    brew install source-to-image

`cd` into argo/model. We're splitting the training and deploying functions of the model into two separate Python files, `train.py` and `Transformer.py`. In our example, all `train.py` does is download the pretrained GPT-2 weights and save the model to a directory (this will eventually be backed by a k8s [PersistentVolumeClaim](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims) so the weights can be picked up by another container). `Transformer.py` is what will be used by Seldon Core when it comes to serving and making predictions. We need to create a class that contains a `predict` method which will be instantiated by Seldon Core on deployment. We can also use the `__init__` method to perform any intialization required. Here's an example borrowed from the Seldon Core docs:

    class MyModel(object):
    """
    Model template. You can load your model parameters in __init__ from a location accessible at runtime
    """

    def __init__(self):
        """
        Add any initialization parameters. These will be passed at runtime from the graph definition parameters defined in your seldondeployment kubernetes resource manifest.
        """
        print("Initializing")

    def predict(self,X,features_names):
        """
        Return a prediction.

        Parameters
        ----------
        X : array-like
        feature_names : array of feature names (optional)
        """
        print("Predict called - will run identity function")
        return X

If you have any dependencies for the scripts (which you almost certainly will!), you'll need to run:

    pip freeze > requirements.txt

To allow `s2i` to fetch the dependencies when it builds the image.

We also need to set up metadata that `s2i` in a `.s2i/environment` file that will be used to build the image and injected into the container's environment:

    MODEL_NAME=Transformer
    API_TYPE=REST
    SERVICE_TYPE=MODEL
    PERSISTENCE=0

With all that in place, we can build our Docker image: 

    s2i build . seldonio/seldon-core-s2i-python3:0.12 transformer

(the `seldonio/seldon-core-s2i-python3:0.12` instructs `s2i` to use that image as a base)

Do a `docker images` and ensure that the image is in place.

## Setting up Argo & Seldon Core

### Argo

### Seldon Core

### PersistentVolumeClaim

As we're going to be splitting the training from the deploying, we need a way of passing the model's parameters between containers. This can be done in many different ways: we could write to a cloud storage service like GCS or S3, or set up something like MinIO to give us a local object store. Instead we're going to set k8s up with a PersistentVolumeClaim which we're going to mount on both containers and have them just read and write from the filesystem. Here's a simple PVC definition:

    kind: PersistentVolumeClaim
    apiVersion: v1
    metadata:
    name: model-parameters-pvc
    spec:
    accessModes: [ "ReadWriteMany" ]
    resources:
        requests:
        storage: 2Gi    

Apply it with `kubectl apply -f pvc.yaml`.

## Workflows

## Submitting Workflow to Argo

Submitting the final workflow to Argo can either be done with `kubectl` or with `argo`:

    argo submit --watch train-deploy.yaml -p model-name="transformer"

## Getting Predictions

Once the workflow has completed successfully, you can use `curl` to talk to the model behind its Ambassador deployment:

    curl -X POST \
         -H 'Content-Type: application/json'   \  -d "{'data': {'names': ['text'], 'ndarray': ['What are we going to do?']}" \
        http://localhost:80/seldon/default/transformer/api/v0.1/predictions

And you'll get a response back that looks like this:

    {
    "meta": {
        "puid": "ruc2mu7j5mbr56cj6kg93eei7p",
        "tags": {
        },
        "routing": {
        },
        "requestPath": {
        "transformer": "transformer"
        },
        "metrics": []
    },
    "strData": "What are we going to do? - \n\nCan't change it without breaking it."    

## Removing the deployed model

You can get a list of deployed models with `kubectl get SeldonDeployment` and the Transformer model can be removed using `kubectl delete SeldonDeployment/transformer`. 