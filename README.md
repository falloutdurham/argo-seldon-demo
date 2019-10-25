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

Before we can run our workflow and deploy our model, we need to install the Argo and Seldon Core operators in our k8s cluster. I'm assuming that you have Helm and Tiller running already. Create a new namespace for our models:

    kubectl create namespace models

### Argo

Firstly, install the `argo` CLI:

    brew install argoproj/tap/argo

Then install the operator and give permissions for the default admin role to create Argo workflows:

    kubectl apply -n models -f https://raw.githubusercontent.com/argoproj/argo/stable/manifests/install.yaml
    kubectl create rolebinding default-admin --clusterrole=admin --serviceaccount=default:default

### Seldon Core

For Seldon Core, we need to install the operator and Ambassador, which we'll be using to serve the models (you can also use Istio):

    helm install seldon-core-operator --name seldon-core --repo https://storage.googleapis.com/seldon-charts --set usageMetrics.enabled=true --namespace models

    helm install stable/ambassador --name ambassador --set crds.keep=false


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

Argo is a workflow orchestrator/scheduler in a similar vein to other frameworks like Oozie or Airflow, but is Kubernetes-native. You can build up flows that are simply a series of sequential steps or move into a full-blown DAG job description. In this basic example, we'll be creating a simple two-step approach with the first step training the model and the second actually deploying it. 

## Templates

The core of Argo is the `template`. This describes either a container to run or a k8s resource to modify. Here's an example of the container-based template:

    - name: training
        inputs:
            parameters:
                - name: model-name
        outputs:
            parameters:
                - name: model-location
                valueFrom: 
                    path: /tmp/model-location
        container:
            image: "{{inputs.parameters.model-name}}"
            imagePullPolicy: IfNotPresent
            command: [python]
            args: ["train.py"]

            volumeMounts:
            - name: model-parameters
                mountPath: /mnt/parameters    

The `container` section is a standard container spec, so we can use all the standard keys there, in this case making sure we run `python train.py` to train the model and mounting our PVC to the required path. We can also pass in parameters and pass them downstream using Jinja2 templating (it feels a lot like using Ansible in practice!), and we can also specify output parameters too! Here, `train.py` will write the final destination of the model's trained parameters to `/tmp/model-location`, which will then be picked up by the next template so it knows where to look for the parameters file (or S3 bucket or wherever).

We can also modify a k8s resource, which we do in the next template:

      - name: deploying
        inputs:
          parameters:
            - name: model-name
            - name: model-location
        resource:      
          action: apply
          successCondition: status.state == Available
          failureCondition: status.failed > 3
          manifest: |
            apiVersion: machinelearning.seldon.io/v1alpha2
            kind: SeldonDeployment
            metadata:
              name: "{{inputs.parameters.model-name}}"
            spec:
              name: "{{inputs.parameters.model-name}}"
              predictors:
              - componentSpecs:
                - spec:
                    volumes:
                    - name: model-parameters
                      persistentVolumeClaim:
                          claimName: model-parameters-pvc
                    containers:
                    - image: "{{inputs.parameters.model-name}}"
                      name: "{{inputs.parameters.model-name}}"
                      env:
                        - name: MODEL_LOCATION
                          value: "{{inputs.parameters.model-location}}"
                      volumeMounts:
                        - mountPath: /mnt/parameters
                          name: model-parameters
                graph:
                  endpoint:
                    type: REST
                  name: "{{inputs.parameters.model-name}}"
                  type: MODEL
                labels:
                  version: v1
                name: "{{inputs.parameters.model-name}}"
                replicas: 1

There's a lot going on here! Let's look at the Argo parts first and then delve into the actual SeldonDeployment resource we're creating. Our inputs are `model-name` and `model-location` (the latter of which will come from the previous template's output), and instead of supplying a `container`, we use a `resource`. We can use all the standard k8s verbs here providing we have permission, and we can also define what we term a successful deployment or a failed one using `successCondition` and `failureCondition`.

Right, now we can look at the SeldonCore manifest! As it can describe a vast assortment of different model deployments (such as A/B testing, canary deployments, and construction of a full graph using different SeldonCore deployments), the `predictors` subsection has plenty of options which we won't need to delve into here (but go read the [docs](https://docs.seldon.io/projects/seldon-core)!). What we care about is just our little transformer model. We're injecting the `model-location` into the environment as `MODEL_LOCATION` so the model can locate the parameters during the initialization phase, and mounting the PVC so the container can get access to the shared storage space.

## Steps

With the templates defined, we can create our two-step workflow! We use `entrypoint` to tell Argo the first step to run, and then it will continue sequentially from there. You'll notice that each step is marked by `- -`; this tells Argo that the next step will not run until the previous has finished - otherwise it will launch them in parallel. Most of what we're doing here is just calling our defined templates and filling in the required parameters. Notice that we can access _global_ parameters with `workflow.parameters.*` and gain access to a step's parameters with something like `steps.step-name.outputs.parameters.parameter`.

    entrypoint: train-deploy  
    
    - name: train-deploy
      steps:
    
      - - name: train-model
          template: training
          arguments:
            parameters:
              - name: model-name
                value: "{{workflow.parameters.model-name}}"
      
      - - name: deploy-model
          template: deploying
          arguments:
            parameters:
              - name: model-name
                value: "{{workflow.parameters.model-name}}"
              - name: model-location
                value: "{{steps.train-model.outputs.parameters.model-location}}"

## Submitting Workflow to Argo

Submitting the final `train-deploy.yaml` workflow to Argo can either be done with `kubectl` or with `argo`:

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

You can get a list of deployed models with `kubectl get SeldonDeployment` and the Transformer model can be removed using `kubectl delete SeldonDeployment/transformer`. (don't just attempt to delete the `deployment` as SeldonCore will just bring it back ;P)