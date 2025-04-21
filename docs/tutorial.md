## Tutorial

### Step 1: Create Azure Resources

First, let's create the necessary Azure resources:

```bash
# Set variables
export UNIQUE_ID=$(openssl rand -hex 3)
export RESOURCE_GROUP="aks-storage-poc"
export LOCATION="centralus"
export AKS_NAME="aks-storage-${UNIQUE_ID}"
export STORAGE_ACCOUNT_NAME="aksstorage${UNIQUE_ID}"
export IDENTITY_NAME="storage-workload-identity"
export BLOB_CONTAINER_NAME="blob-container"
export FILE_SHARE_NAME="storage-share"

# Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION --tags "project=aks-storage-poc"

# Create user-assigned managed identity
az identity create \
  --resource-group $RESOURCE_GROUP \
  --name $IDENTITY_NAME

# Store identity client ID and principal ID
export IDENTITY_CLIENT_ID=$(az identity show --resource-group $RESOURCE_GROUP --name $IDENTITY_NAME --query clientId -o tsv)
export IDENTITY_PRINCIPAL_ID=$(az identity show --resource-group $RESOURCE_GROUP --name $IDENTITY_NAME --query principalId -o tsv)

# Create storage account with appropriate key access settings
az storage account create \
  --resource-group $RESOURCE_GROUP \
  --name $STORAGE_ACCOUNT_NAME \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-blob-public-access false \
  --allow-shared-key-access false  # Set to true for Azure Files

# Get storage account ID
export STORAGE_ACCOUNT_ID=$(az storage account show --resource-group $RESOURCE_GROUP --name $STORAGE_ACCOUNT_NAME --query id -o tsv)

# Create blob container
az storage container create \
  --name $BLOB_CONTAINER_NAME \
  --account-name $STORAGE_ACCOUNT_NAME \
  --auth-mode login

# Create AKS cluster with OIDC issuer and workload identity enabled
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --node-count 1 \
  --enable-managed-identity \
  --enable-oidc-issuer \
  --enable-workload-identity \
  --generate-ssh-keys

# Get credentials
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --overwrite-existing

# Get the OIDC issuer URL
export AKS_OIDC_ISSUER="$(az aks show --name $AKS_NAME \
  --resource-group $RESOURCE_GROUP \
  --query "oidcIssuerProfile.issuerUrl" \
  --output tsv)"

# Get the node resource group
export NODE_RESOURCE_GROUP=$(az aks show --resource-group $RESOURCE_GROUP --name $AKS_NAME --query nodeResourceGroup -o tsv)
```

### Step 2: Configure Workload Identity for AKS

Next, let's set up the necessary Kubernetes resources for workload identity:

```bash
# Create a service account and annotate with client ID
export SERVICE_ACCOUNT_NAME="storage-sa"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  annotations:
    azure.workload.identity/client-id: "${IDENTITY_CLIENT_ID}"
  name: "${SERVICE_ACCOUNT_NAME}"
EOF

# Create federated identity credential
az identity federated-credential create \
  --name "storage-federated-credential" \
  --identity-name "${IDENTITY_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --issuer "${AKS_OIDC_ISSUER}" \
  --subject "system:serviceaccount:default:${SERVICE_ACCOUNT_NAME}" \
  --audience "api://AzureADTokenExchange"

# Assign required permissions to the managed identity
# 1. Storage Account Key Operator Service Role
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage Account Key Operator Service Role" \
  --scope $STORAGE_ACCOUNT_ID

# 2. Storage Blob Data Contributor (for Blob Storage)
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage Blob Data Contributor" \
  --scope $STORAGE_ACCOUNT_ID

# 3. Reader role on the node resource group
NODE_RG_ID=$(az group show --name "$NODE_RESOURCE_GROUP" --query id -o tsv)
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Reader" \
  --scope "$NODE_RG_ID"
```

### Step 3: Install (OSS) Blob CSI Driver'

> Note: AKS normally installs a blob CSI driver automatically using the   --enable-blob-driver flag.  We are installing OSS latest version.

#### Helm Install

```bash
# Add the helm repository
helm repo add blob-csi-driver https://raw.githubusercontent.com/kubernetes-sigs/blob-csi-driver/master/charts
helm repo update

# Install the Blob CSI driver with inline values
DRIVER_VERSION="v1.26.1"
helm upgrade --install blob-csi blob-csi-driver/blob-csi-driver \
  --namespace kube-system \
  --version $DRIVER_VERSION \
  --set blobfuse2.enabled=true \
  --set workloadIdentity.clientID=${IDENTITY_CLIENT_ID} \
  --set "node.tokenRequests[0].audience=api://AzureADTokenExchange" \
  --set controller.replicas=1 \
  --set controller.runOnControlPlane=false \
  --set "node.tolerations[0].key=kubernetes.azure.com/role" \
  --set "node.tolerations[0].operator=Equal" \
  --set "node.tolerations[0].value=agent" \
  --set "node.tolerations[0].effect=NoSchedule"

# Wait for pod to start
kubectl get pods -n kube-system --watch | grep "csi-blob-"
```

### Step 3: Persistent Volume Configuration

#### Azure Blob Configuration

```bash
# Create persistent PV for Blob Storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: blob-persistent-pv
  annotations:
    pv.kubernetes.io/provisioned-by: blob.csi.azure.com
spec:
  capacity:
    storage: 5Gi
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
    volumeHandle: ${RESOURCE_GROUP}#${STORAGE_ACCOUNT_NAME}#${BLOB_CONTAINER_NAME}
    volumeAttributes:
      storageaccount: ${STORAGE_ACCOUNT_NAME}
      containerName: ${BLOB_CONTAINER_NAME}
      clientID: ${IDENTITY_CLIENT_ID}
      resourcegroup: ${RESOURCE_GROUP}
      protocol: fuse
EOF

# Create PVC for Blob Storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-blob-persistent
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
  volumeName: blob-persistent-pv
  storageClassName: azureblob-fuse-premium
EOF

# Create a job to validate the storage mount capabilities
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: blob-creator
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_NAME}
      containers:
      - name: blob-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from Azure Blob" > /mnt/blob/test.txt
          ls -l /mnt/blob/test.txt
          cat /mnt/blob/test.txt
        volumeMounts:
        - name: blob-storage
          mountPath: /mnt/blob
      volumes:
      - name: blob-storage
        persistentVolumeClaim:
          claimName: pvc-blob-persistent
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/blob-creator --timeout=60s
kubectl logs job/blob-creator
```

#### Azure Files Configuration

As noted above, Azure Files does not support keyless access. Therefore, we need to enable shared key access, create a file share, and assign the appropriate RBAC role.

```bash
# Update storage account to allow shared key access
az storage account update \
  --resource-group $RESOURCE_GROUP \
  --name $STORAGE_ACCOUNT_NAME \
  --allow-shared-key-access true \
  --query "allowSharedKeyAccess"

# NOTE: Pause for about 30 seconds to allow the storage account to update

# Create file share (doesn't support --auth-mode login)
az storage share create \
  --name $FILE_SHARE_NAME \
  --account-name $STORAGE_ACCOUNT_NAME

# Storage File Data SMB Share Contributor (for Azure Files)
az role assignment create \
  --assignee $IDENTITY_PRINCIPAL_ID \
  --role "Storage File Data SMB Share Contributor" \
  --scope $STORAGE_ACCOUNT_ID
```

```bash
# Create persistent PV for Azure Files
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: file-persistent-pv
  annotations:
    pv.kubernetes.io/provisioned-by: file.csi.azure.com
spec:
  capacity:
    storage: 5Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: azurefile-csi
  csi:
    driver: file.csi.azure.com
    volumeHandle: ${RESOURCE_GROUP}#${STORAGE_ACCOUNT_NAME}#${FILE_SHARE_NAME}
    volumeAttributes:
      storageaccount: ${STORAGE_ACCOUNT_NAME}
      shareName: ${FILE_SHARE_NAME}
      clientID: ${IDENTITY_CLIENT_ID}
      resourcegroup: ${RESOURCE_GROUP}
      protocol: smb
EOF

# Create PVC for Azure Files
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-file-persistent
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
  volumeName: file-persistent-pv
  storageClassName: azurefile-csi
EOF

# Create a job to validate the storage mount capabilities
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: file-creator
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_NAME}
      containers:
      - name: file-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from Azure Files" > /mnt/file/test.txt
          ls -l /mnt/file/test.txt
          cat /mnt/file/test.txt
        volumeMounts:
        - name: file-storage
          mountPath: /mnt/file
      volumes:
      - name: file-storage
        persistentVolumeClaim:
          claimName: pvc-file-persistent
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/file-creator --timeout=60s
kubectl logs job/file-creator
```

### Step 4: Dynamic Volume Configuration

#### Azure Blob Storage Configuration

```bash
# Create PVC that will dynamically provision storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-blob-dynamic
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azureblob-fuse-premium
  resources:
    requests:
      storage: 5Gi
EOF

# NOTE: Pause for about 30 seconds to allow the storage account to create

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: dynamic-blob-creator
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_NAME}
      containers:
      - name: blob-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from dynamic blob provisioning" > /mnt/dynamic/test.txt
          ls -l /mnt/dynamic/test.txt
          cat /mnt/dynamic/test.txt
        volumeMounts:
        - name: dynamic
          mountPath: /mnt/dynamic
      volumes:
      - name: dynamic
        persistentVolumeClaim:
          claimName: pvc-blob-dynamic
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/dynamic-blob-creator --timeout=60s
kubectl logs job/dynamic-blob-creator
```

#### Azure Files Configuration

```bash
# Create PVC that will dynamically provision storage
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-file-dynamic
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azurefile-csi
  resources:
    requests:
      storage: 5Gi
EOF

# NOTE: Pause for about 30 seconds to allow the storage account to create

# Create a job to validate the storage
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: dynamic-file-creator
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_NAME}
      containers:
      - name: file-creator
        image: mcr.microsoft.com/azure-cli
        command: ["/bin/bash", "-c"]
        args:
        - |
          echo "Hello from dynamic file provisioning" > /mnt/dynamic/test.txt
          ls -l /mnt/dynamic/test.txt
          cat /mnt/dynamic/test.txt
        volumeMounts:
        - name: dynamic
          mountPath: /mnt/dynamic
      volumes:
      - name: dynamic
        persistentVolumeClaim:
          claimName: pvc-file-dynamic
      restartPolicy: Never
EOF

# Wait for job to complete and show logs
kubectl wait --for=condition=complete job/dynamic-file-creator --timeout=60s
kubectl logs job/dynamic-file-creator
```