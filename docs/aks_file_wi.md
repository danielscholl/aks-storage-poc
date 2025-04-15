# Azure Kubernetes Service (AKS) with Workload Identity and Azure Files Integration Guide

> [!WARNING]
> **Important**: There is currently no working solution for Azure Files with keyless access (`allow-shared-key-access` set to false). Both static and dynamic provisioning methods require storage account keys to be enabled. This makes Azure Files incompatible with policies requiring keyless access.

## Overview
This guide walks you through setting up an AKS cluster with workload identity authentication to access Azure Files. It covers two use cases:
1. Static provisioning: Using an existing storage account and file share
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
az group create -n $RESOURCE_GROUP -l $LOCATION --tags StorageType=File

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

## Step 3: Create Storage Account and File Share

> [!NOTE]
> The storage account must be created with `allow-shared-key-access` set to true.

```bash
# Create storage account and get ID
STORAGE_ACCOUNT_ID=$(az storage account create \
  --resource-group $RESOURCE_GROUP \
  --name $STORAGE_ACCOUNT \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-shared-key-access true \
  --query id -o tsv)

# Create file share
az storage share create \
  --name "myshare" \
  --account-name $STORAGE_ACCOUNT
```

## Step 4: Assign Required Roles
```bash
# Assign Storage Account Key Operator Service Role to allow the CSI Driver to iterate storage account keys
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage Account Key Operator Service Role" \
  --scope $STORAGE_ACCOUNT_ID

# Storage File Data SMB Share Contributor role on storage account to read and write to the file share
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage File Data SMB Share Contributor" \
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
  --enable-workload-identity

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
# Create static PV pointing to existing storage account and share
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: azurefile-pv
  annotations:
    pv.kubernetes.io/provisioned-by: file.csi.azure.com
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: azurefile-csi
  mountOptions:
    - dir_mode=0777
    - file_mode=0777
    - uid=0
    - gid=0
    - mfsymlinks
    - cache=strict
    - nosharesock
    - vers=3.0
    - actimeo=30
    - noperm
    - serverino
  csi:
    driver: file.csi.azure.com
    volumeHandle: ${RESOURCE_GROUP}#${STORAGE_ACCOUNT}#myshare
    volumeAttributes:
      storageaccount: ${STORAGE_ACCOUNT}
      shareName: myshare
      clientID: ${IDENTITY_CLIENT_ID}
      resourcegroup: ${RESOURCE_GROUP}
      protocol: smb
EOF

# Create PVC that binds to the static PV
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-static-file
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 10Gi
  volumeName: azurefile-pv
  storageClassName: azurefile-csi
EOF

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: static-file-creator
spec:
  template:
    spec:
      serviceAccountName: storage-sa
      containers:
      - name: file-creator
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
          claimName: pvc-static-file
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/static-file-creator --timeout=60s
kubectl logs job/static-file-creator
```

## Step 8: Dynamic Provisioning (Use Case 2)

### Default Azure Files StorageClasses in AKS

| StorageClass Name | Description | Performance Tier | CSI Driver | Use Case |
|------------------|-------------|------------------|------------|----------|
| `azurefile` | Legacy in-tree driver (deprecated) | Standard (HDD) | ❌ | Legacy compatibility only |
| `azurefile-csi` | Modern CSI driver | Standard (HDD) | ✅ | General purpose workloads |
| `azurefile-csi-premium` | Modern CSI driver with premium storage | Premium (SSD) | ✅ | High-performance workloads |
| `azurefile-premium` | Modern CSI driver with premium storage | Premium (SSD) | ✅ | High-performance workloads |


```bash
# Create PVC that will dynamically provision storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-dynamic-file
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azurefile-csi
  resources:
    requests:
      storage: 5Gi
EOF

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: dynamic-file-creator
spec:
  template:
    spec:
      serviceAccountName: storage-sa
      containers:
      - name: file-creator
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
          claimName: pvc-dynamic-file
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/dynamic-file-creator --timeout=60s
kubectl logs job/dynamic-file-creator
```
