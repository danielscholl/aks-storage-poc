# Azure Kubernetes Service (AKS) with Workload Identity and Azure Blob Integration Guide

## Important Note
**Dynamic provisioning is currently not supported for keyless Blob storage access** as it cannot create storage accounts with access keys completely disabled (`allow-shared-key-access` set to false). This makes it incompatible with policies requiring keyless access. Only static provisioning (Use Case 1) is supported for keyless Blob storage access.

## Overview
This guide walks you through setting up an AKS cluster with workload identity authentication to access Azure Blob Storage. It covers two use cases:
1. Static provisioning: Using an existing storage account and blob container
2. Dynamic provisioning: Letting Kubernetes create storage resources automatically

## Prerequisites
- Azure CLI installed and logged in (`az login`)
- kubectl installed

## Step 1: Environment Setup
Set these environment variables:
```bash
# Required variables
export UNIQUE_ID=$(openssl rand -hex 3)
export NAME="poc${UNIQUE_ID}"
export RESOURCE_GROUP="$NAME-rg"
export STORAGE_ACCOUNT="${NAME}sa"
export IDENTITY="$NAME-identity"
export CLUSTER="$NAME-aks"
export LOCATION="eastus"
```

## Step 2: Create Managed Identity
```bash
# Create resource group if needed
az group create -n $RESOURCE_GROUP -l $LOCATION --tags StorageType=Blob

# Create managed identity and get IDs
IDENTITY_CLIENT_ID=$(az identity create \
  --resource-group $RESOURCE_GROUP \
  --name $IDENTITY \
  --location $LOCATION \
  --query clientId -o tsv)

IDENTITY_PRINCIPAL_ID=$(az identity show \
  --resource-group $RESOURCE_GROUP \
  --name $IDENTITY \
  --query principalId -o tsv)
```

## Step 3: Create Storage Account and Blob Container

> [!NOTE]
> The storage account can be created with `allow-shared-key-access` set to false to restrict keyless access.

```bash
# Create storage account and get ID
STORAGE_ACCOUNT_ID=$(az storage account create \
  --resource-group $RESOURCE_GROUP \
  --name $STORAGE_ACCOUNT \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-shared-key-access false \
  --query id -o tsv)

# Create blob container
az storage container create \
  --name "blob-container" \
  --account-name $STORAGE_ACCOUNT \
  --auth-mode login
```

## Step 4: Assign Required Roles
```bash
# Assign Storage Account Key Operator Service Role to allow the CSI Driver to iterate storage account keys
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage Account Key Operator Service Role" \
  --scope $STORAGE_ACCOUNT_ID

# Storage Blob Data Contributor role on storage account to read and write to the blob container
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage Blob Data Contributor" \
  --scope $STORAGE_ACCOUNT_ID
```

## Step 5: Create AKS Cluster
```bash
# Create AKS cluster with workload identity enabled
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER \
  --location $LOCATION \
  --node-count 1 \
  --enable-managed-identity \
  --enable-oidc-issuer \
  --enable-workload-identity \
  --enable-blob-driver

# Get AKS credentials
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER \
  --overwrite-existing

# Get OIDC issuer URL from AKS cluster
OIDC_ISSUER=$(az aks show \
  --name $CLUSTER \
  --resource-group $RESOURCE_GROUP \
  --query "oidcIssuerProfile.issuerUrl" \
  --output tsv)
```

## Step 6: Configure Workload Identity
```bash
# Create service account with workload identity annotation
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: storage-sa
  annotations:
    azure.workload.identity/client-id: $IDENTITY_CLIENT_ID
EOF

# Create federated identity credential
az identity federated-credential create \
  --name "storage-credential" \
  --identity-name $IDENTITY \
  --resource-group $RESOURCE_GROUP \
  --issuer $OIDC_ISSUER \
  --subject "system:serviceaccount:default:storage-sa" \
  --audience "api://AzureADTokenExchange"
```

## Step 7: Static Provisioning (Use Case 1)
```bash
# Create static PV pointing to existing storage account and container
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: blob-static-pv
  annotations:
    pv.kubernetes.io/provisioned-by: blob.csi.azure.com
spec:
  capacity:
    storage: 1Pi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: azureblob-fuse-premium
  mountOptions:
    - -o allow_other
    - --file-cache-timeout-in-seconds=120
    - --use-attr-cache=true
    - --cancel-list-on-mount-seconds=0
    - --log-level=LOG_DEBUG
  csi:
    driver: blob.csi.azure.com
    volumeHandle: ${RESOURCE_GROUP}#${STORAGE_ACCOUNT}#blob-container
    volumeAttributes:
      storageaccount: ${STORAGE_ACCOUNT}
      containerName: blob-container
      clientID: ${IDENTITY_CLIENT_ID}
      resourcegroup: ${RESOURCE_GROUP}
      protocol: fuse
EOF

# Create PVC that binds to the static PV
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-blob
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
  volumeName: blob-static-pv
  storageClassName: azureblob-fuse-premium
EOF

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: static-blob-creator
spec:
  template:
    spec:
      serviceAccountName: storage-sa
      containers:
      - name: blob-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from static provisioning" > /mnt/static/test.txt
          ls -l /mnt/static/test.txt
          cat /mnt/static/test.txt
        volumeMounts:
        - name: static
          mountPath: /mnt/static
      volumes:
      - name: static
        persistentVolumeClaim:
          claimName: pvc-blob
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/static-blob-creator --timeout=60s
kubectl logs job/static-blob-creator
```

## Step 8: Dynamic Provisioning (Use Case 2)

### Default Azure Blob StorageClasses in AKS

| StorageClass Name | Description | Performance Tier | CSI Driver | Use Case |
|------------------|-------------|------------------|------------|----------|
| `azureblob-nfs-premium` | Modern CSI driver with premium storage | Premium (SSD) | ✅ | High-performance workloads |
| `azureblob-fuse-premium` | Modern CSI driver with FUSE | Premium (SSD) | ✅ | High-performance workloads with FUSE |

```bash
# Create PVC that will dynamically provision storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: dynamic-blob-pvc
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azureblob-nfs-premium
  resources:
    requests:
      storage: 5Gi
EOF

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: dynamic-blob-creator
spec:
  template:
    spec:
      serviceAccountName: storage-sa
      containers:
      - name: blob-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from dynamic provisioning" > /mnt/dynamic/test.txt
          ls -l /mnt/dynamic/test.txt
          cat /mnt/dynamic/test.txt
        volumeMounts:
        - name: dynamic
          mountPath: /mnt/dynamic
      volumes:
      - name: dynamic
        persistentVolumeClaim:
          claimName: dynamic-blob-pvc
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/dynamic-blob-creator --timeout=60s
kubectl logs job/dynamic-blob-creator
```