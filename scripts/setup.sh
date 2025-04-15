#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Breaking Free from Keys: AKS & Azure Storage Integration PoC
#
# Overview:
# 1. Prerequisites & Configuration Generation:
#    - Checks required tools, verifies Azure login, and generates a unique ID.
#
# 2. Azure Resources Creation:
#    - Creates Resource Group, Managed Identity, and Storage Account.
#
# 3. AKS Cluster & Workload Identity Setup:
#    - Creates an AKS cluster and configures workload identity to secure access.
#    - **NEW:** Updates the AKS cluster to enable the native Blob CSI driver.
#
# 4. Storage Provisioning:
#    - Azure Files provisioning (unchanged).
#    - Blob Storage provisioning now leverages the built-in storage classes:
#         - Dynamic: Creates a PVC using the built-in **azureblob-nfs-premium** class.
#         - Static: Creates a PersistentVolume (PV) and PVC using **azureblob-nfs-premium**.
#
# 5. Validation:
#    - Runs jobs and pods to validate file creation and accessibility.
#
# 6. Final Summary:
#    - Displays resource details and useful commands.
#
# Usage:
#   ./script.sh [unique-id]
#
# Options:
#   -h, --help   Show help message.
#
# -----------------------------------------------------------------------------

# Exit on any error
set -e

# --------------------------
# COLOR DEFINITIONS
# --------------------------
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Log level (set to INFO to hide TRACE messages)
export LOG_LEVEL=${LOG_LEVEL:-"TRACE"}

# --------------------------
# HELPER FUNCTIONS
# --------------------------
print_section_header() {
  local title=$1
  echo -e "\n${BLUE}═════════════════════════════════════════════════════════════════${NC}"
  echo -e "${BLUE}   ${title}${NC}"
  echo -e "${BLUE}═════════════════════════════════════════════════════════════════${NC}\n"
}

print_subsection_header() {
  local title=$1
  echo -e "\n${GREEN}┌─────────────────────────────────────────────────────────────┐${NC}"
  echo -e "${GREEN}│ ${title}${NC}"
  echo -e "${GREEN}└─────────────────────────────────────────────────────────────┘${NC}"
}

print_action_header() {
  local title=$1
  echo -e "${YELLOW}▶ ${title}${NC}"
}

log()   { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

trace() {
  if [[ "${LOG_LEVEL}" == "TRACE" ]]; then
    local message=$1
    if [[ "$message" == *"kubectl apply"* ]]; then
      echo -e "${BLUE}[K8S APPLY]${NC} Creating/updating Kubernetes resource:"
      local yaml_content=$(echo "$message" | awk '/cat <<EOF/,/EOF/{if (!/cat <<EOF/ && !/EOF/) print}')
      if [[ -n "$yaml_content" ]]; then
        local kind=$(echo "$yaml_content" | grep -E "^kind:" | awk '{print $2}')
        local name=$(echo "$yaml_content" | grep -E "^  name:" | awk '{print $2}')
        local namespace=$(echo "$yaml_content" | grep -E "^  namespace:" | awk '{print $2}')
        echo -e "${YELLOW}Resource:${NC} $kind/$name ${namespace:+in namespace $namespace}"
        echo -e "${BLUE}---${NC}"
        echo "$yaml_content" | sed 's/^/  /'
        echo -e "${BLUE}---${NC}"
      fi
    else
      echo -e "${BLUE}[DEBUG]${NC} $message"
    fi
  fi
}

verify() {
  if [[ -z "$1" ]]; then
    error "$2"
  fi
}

check_command() {
  if ! command -v $1 &> /dev/null; then
    error "$1 is required but not installed. Please install it first."
  fi
}

wait_for_resource() {
  local resource_type=$1
  local resource_name=$2
  local namespace=$3
  local wait_msg=$4
  local success_msg=$5
  local max_retries=${6:-30}
  local retries=0

  echo -n "$wait_msg"
  while [[ $retries -lt $max_retries ]]; do
    if [[ -n "$namespace" ]]; then
      status=$(kubectl get $resource_type $resource_name -n $namespace 2>/dev/null)
    else
      status=$(kubectl get $resource_type $resource_name 2>/dev/null)
    fi

    if [[ $? -eq 0 ]]; then
      echo -e "\n$success_msg"
      return 0
    fi

    sleep 5
    echo -n "."
    ((retries++))
  done

  echo ""
  error "Timed out waiting for $resource_type/$resource_name"
}

# --------------------------
# TEMPLATE PROCESSING FUNCTIONS
# --------------------------
process_template() {
  local template_file=$1
  local output_file=$2
  shift 2
  local vars=("$@")

  # Create a temporary file for the processed template
  local temp_file=$(mktemp)

  # Copy the template to the temporary file
  cp "$template_file" "$temp_file"

  # Replace each variable in the template
  for var in "${vars[@]}"; do
    local name=${var%%=*}
    local value=${var#*=}
    sed -i '' "s/\${${name}}/${value}/g" "$temp_file"
  done

  # Move the processed template to the output file
  mv "$temp_file" "$output_file"
}

# --------------------------
# CONFIGURATION & UTILITY FUNCTIONS
# --------------------------
generate_config() {
  SUBSCRIPTION_ID=$(az account show --query id -o tsv 2>/dev/null || echo "")
  if [[ -z "$SUBSCRIPTION_ID" ]]; then
    error "No Azure subscription found. Please login with 'az login'"
  fi

  if [[ -n "$1" ]]; then
    UNIQUE_ID="$1"
    log "Using provided unique ID: $UNIQUE_ID"
  else
    UNIQUE_ID=$(openssl rand -hex 3)
    log "Generated random unique ID: $UNIQUE_ID"
  fi

  RESOURCE_GROUP="aks-storage-poc-${UNIQUE_ID}"
  LOCATION="eastus"
  AKS_NAME="aks-storage-${UNIQUE_ID}"
  STORAGE_ACCOUNT_NAME="aksstorage${UNIQUE_ID}"
  IDENTITY_NAME="storage-workload-identity"
  FILE_SHARE_NAME="storage-share"
  SERVICE_ACCOUNT_NAME="storage-sa"
  SERVICE_ACCOUNT_NAMESPACE="storage-demo"
}

# --------------------------
# PREREQUISITES CHECK
# --------------------------
check_prerequisites() {
  print_subsection_header "Prerequisites Check"
  log "Checking prerequisites..."
  check_command "az"
  check_command "kubectl"
  check_command "openssl"

  if ! az account show &> /dev/null; then
    error "Not logged in to Azure. Please run 'az login' first."
  fi

  log "Prerequisites check passed."
}

# --------------------------
# AZURE RESOURCE CREATION FUNCTIONS
# --------------------------
create_resource_group() {
  local rg_name=$1
  local location=$2
  verify "$rg_name" "create_resource_group-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$location" "create_resource_group-ERROR: Argument (LOCATION) not received"

  print_subsection_header "Creating Resource Group"
  trace "Checking resource group '$rg_name'..."
  local result=$(az group show --name $rg_name 2>/dev/null)

  if [[ -z "$result" ]]; then
    log "Creating resource group '$rg_name' in '$location'..."
    az group create --name $rg_name \
      --location $location \
      --tags CREATED_BY="AKS-Storage-PoC-Script" CREATED_DATE="$(date +%Y-%m-%d)" \
      --output none
    log "Resource group created successfully."
  else
    log "Resource group '$rg_name' already exists."
  fi
}

create_managed_identity() {
  local rg_name=$1
  local identity_name=$2
  local location=$3

  verify "$rg_name" "create_managed_identity-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$identity_name" "create_managed_identity-ERROR: Argument (IDENTITY_NAME) not received"
  verify "$location" "create_managed_identity-ERROR: Argument (LOCATION) not received"

  print_subsection_header "Creating User-Assigned Managed Identity"
  trace "Checking managed identity '$identity_name'..."
  local result=$(az identity show --resource-group $rg_name --name $identity_name 2>/dev/null)

  if [[ -z "$result" ]]; then
    log "Creating user-assigned managed identity '$identity_name'..."
    az identity create \
      --resource-group $rg_name \
      --name $identity_name \
      --location $location \
      --output none
    log "Identity created successfully."
    trace "Waiting for identity to propagate through AAD (30 seconds)..."
    sleep 30
  else
    log "Identity '$identity_name' already exists."
  fi

  IDENTITY_CLIENT_ID=$(az identity show \
    --resource-group $rg_name \
    --name $identity_name \
    --query clientId -o tsv)

  IDENTITY_PRINCIPAL_ID=$(az identity show \
    --resource-group $rg_name \
    --name $identity_name \
    --query principalId -o tsv)
}

create_storage_account() {
  local rg_name=$1
  local storage_name=$2
  local location=$3
  local file_share_name=$4
  local identity_principal_id=$5

  verify "$rg_name" "create_storage_account-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$storage_name" "create_storage_account-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$location" "create_storage_account-ERROR: Argument (LOCATION) not received"
  verify "$file_share_name" "create_storage_account-ERROR: Argument (FILE_SHARE_NAME) not received"
  verify "$identity_principal_id" "create_storage_account-ERROR: Argument (IDENTITY_PRINCIPAL_ID) not received"

  print_subsection_header "Creating Storage Account"
  trace "Checking storage account '$storage_name'..."
  local result=$(az storage account show --resource-group $rg_name --name $storage_name 2>/dev/null)

  if [[ -z "$result" ]]; then
    log "Creating storage account '$storage_name'..."
    az storage account create \
      --resource-group $rg_name \
      --name $storage_name \
      --location $location \
      --sku Standard_LRS \
      --kind StorageV2 \
      --allow-shared-key-access true \
      --default-action Allow \
      --output none
    log "Storage account created successfully."


  else
    log "Storage account '$storage_name' already exists."
  fi

  STORAGE_ACCOUNT_ID=$(az storage account show \
    --resource-group $rg_name \
    --name $storage_name \
    --query id -o tsv)

  print_subsection_header "Creating Blob Container"
  local container_name="blob-container"
  trace "Checking blob container '$container_name'..."
  local container_exists=$(az storage container exists \
    --name $container_name \
    --account-name $storage_name \
    --auth-mode login \
    --query exists -o tsv 2>/dev/null || echo "false")

  if [[ "$container_exists" == "false" ]]; then
    log "Creating blob container '$container_name' using Storage Resource Provider API..."
    az storage container create \
      --name $container_name \
      --account-name $storage_name \
      --auth-mode login \
      --output none
    log "Blob container created successfully."
  else
    log "Blob container '$container_name' already exists."
  fi

  print_subsection_header "Creating File Share"
  trace "Checking file share '$file_share_name'..."
  local share_exists=$(az storage share exists \
    --name $file_share_name \
    --account-name $storage_name \
    --auth-mode login \
    --query exists -o tsv 2>/dev/null || echo "false")

  if [[ "$share_exists" == "false" ]]; then
      log "Creating file share '$file_share_name' using Storage Resource Provider API..."
      az rest --method put \
        --uri "https://management.azure.com${STORAGE_ACCOUNT_ID}/fileServices/default/shares/${file_share_name}?api-version=2023-01-01" \
        --body '{"properties": {}}' \
        --output none
      log "File share created successfully."
    else
      log "File share '$file_share_name' already exists."
    fi
}

create_aks_cluster() {
  local rg_name=$1
  local aks_name=$2
  local location=$3

  verify "$rg_name" "create_aks_cluster-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$aks_name" "create_aks_cluster-ERROR: Argument (AKS_NAME) not received"
  verify "$location" "create_aks_cluster-ERROR: Argument (LOCATION) not received"

  print_subsection_header "Creating AKS Cluster"
  trace "Checking AKS cluster '$aks_name'..."
  local result=$(az aks show --resource-group $rg_name --name $aks_name 2>/dev/null)

  if [[ -z "$result" ]]; then
    log "Creating AKS cluster '$aks_name'..."
    az aks create \
      --resource-group $rg_name \
      --name $aks_name \
      --location $location \
      --node-count 1 \
      --enable-managed-identity \
      --enable-oidc-issuer \
      --enable-workload-identity \
      --enable-blob-driver \
      --output none
    log "AKS cluster created successfully."
  else
    log "AKS cluster '$aks_name' already exists."
  fi

  log "Retrieving AKS credentials..."
  az aks get-credentials \
    --resource-group $rg_name \
    --name $aks_name \
    --overwrite-existing \
    --output none

  AKS_OIDC_ISSUER=$(az aks show \
    --name $aks_name \
    --resource-group $rg_name \
    --query "oidcIssuerProfile.issuerUrl" \
    --output tsv)

  log "AKS cluster configured and ready."
  log "OIDC issuer URL: $AKS_OIDC_ISSUER"

  trace "Waiting for the AKS cluster to stabilize..."
  sleep 30

  NODE_RESOURCE_GROUP=$(az aks show \
    --resource-group $rg_name \
    --name $aks_name \
    --query nodeResourceGroup -o tsv)
}

# --------------------------
# ROLE ASSIGNMENT FUNCTIONS
# --------------------------
assign_all_roles() {
  local rg_name=$1
  local identity_principal_id=$2
  local storage_account_id=$3
  local node_resource_group=$4

  verify "$rg_name" "assign_all_roles-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$identity_principal_id" "assign_all_roles-ERROR: Argument (IDENTITY_PRINCIPAL_ID) not received"
  verify "$storage_account_id" "assign_all_roles-ERROR: Argument (STORAGE_ACCOUNT_ID) not received"
  verify "$node_resource_group" "assign_all_roles-ERROR: Argument (NODE_RESOURCE_GROUP) not received"

  print_subsection_header "Assigning RBAC Roles"
  log "Starting role assignments for managed identity..."

  #############
  # Resource group role assignment
  print_action_header "Resource Group Reader Role"
  log "Assigning Reader role to managed identity for the resource group..."
  RESOURCE_GROUP_ID=$(az group show --name "$rg_name" --query id -o tsv)
  az role assignment create \
    --assignee "$identity_principal_id" \
    --role "Reader" \
    --scope "$RESOURCE_GROUP_ID" \
    --output none
  log "Reader role assigned to the managed identity for the resource group."

  #############
  # Node resource group role assignment
  print_action_header "Node Resource Group Reader Role"
  log "Assigning Reader role to the managed identity for node resource group..."
  NODE_RG_ID=$(az group show --name "$node_resource_group" --query id -o tsv)
  if [[ -z "$NODE_RG_ID" ]]; then
    warn "Could not get resource ID for node resource group '$node_resource_group'. Skipping Reader role assignment."
  else
    az role assignment create \
      --assignee $identity_principal_id \
      --role "Reader" \
      --scope "$NODE_RG_ID" \
      --output none
    log "Reader role assigned for the node resource group."
  fi

  #############
  # Storage account role assignments
  print_action_header "Storage Account Contributor Role"
  log "Assigning Storage Account Contributor role..."
  az role assignment create \
    --assignee $identity_principal_id \
    --role "Storage Account Contributor" \
    --scope $storage_account_id \
    --output none

  print_action_header "Storage File Data SMB Share Contributor Role"
  log "Assigning Storage File Data SMB Share Contributor role..."
  az role assignment create \
    --assignee $identity_principal_id \
    --role "Storage File Data SMB Share Contributor" \
    --scope $storage_account_id \
    --output none

  print_action_header "Storage File Data SMB Share Reader Role"
  log "Assigning Storage File Data SMB Share Reader role..."
  az role assignment create \
    --assignee $identity_principal_id \
    --role "Storage File Data SMB Share Reader" \
    --scope $storage_account_id \
    --output none

  print_action_header "Storage Blob Data Contributor Role"
  log "Assigning Storage Blob Data Contributor role..."
  az role assignment create \
    --assignee $identity_principal_id \
    --role "Storage Blob Data Contributor" \
    --scope $storage_account_id \
    --output none

  print_action_header "Storage Account Key Operator Service Role"
  log "Assigning Storage Account Key Operator Service Role..."
  az role assignment create \
    --assignee $identity_principal_id \
    --role "Storage Account Key Operator Service Role" \
    --scope $storage_account_id \
    --output none

  log "All role assignments completed successfully."
  trace "Waiting for role assignments to propagate..."
  sleep 30
}

# --------------------------
# KUBERNETES CONFIGURATION FUNCTIONS
# --------------------------
configure_workload_identity() {
  local rg_name=$1
  local identity_name=$2
  local client_id=$3
  local oidc_issuer=$4
  local sa_namespace=$5
  local sa_name=$6

  verify "$rg_name" "configure_workload_identity-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$identity_name" "configure_workload_identity-ERROR: Argument (IDENTITY_NAME) not received"
  verify "$client_id" "configure_workload_identity-ERROR: Argument (CLIENT_ID) not received"
  verify "$oidc_issuer" "configure_workload_identity-ERROR: Argument (OIDC_ISSUER) not received"
  verify "$sa_namespace" "configure_workload_identity-ERROR: Argument (SERVICE_ACCOUNT_NAMESPACE) not received"
  verify "$sa_name" "configure_workload_identity-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"

  trace "Ensuring namespace '$sa_namespace' exists..."
  kubectl create namespace "$sa_namespace" 2>/dev/null || true

  trace "Checking for existing service account '$sa_name'..."
  local sa_exists=$(kubectl get serviceaccount -n "$sa_namespace" "$sa_name" 2>/dev/null || echo "")

  if [[ -z "$sa_exists" ]]; then
    log "Creating service account '$sa_name' with workload identity annotation..."
    process_template "templates/serviceaccount.yaml" "serviceaccount-processed.yaml" \
      "CLIENT_ID=$client_id" \
      "NAME=$sa_name" \
      "NAMESPACE=$sa_namespace"
    kubectl apply -f serviceaccount-processed.yaml
    rm serviceaccount-processed.yaml
    log "Service account created successfully."
  else
    log "Service account '$sa_name' already exists. Updating workload identity annotation..."
    kubectl patch serviceaccount $sa_name -n $sa_namespace -p '{"metadata":{"annotations":{"azure.workload.identity/client-id":"'$client_id'"}}}'
  fi

  log "Setting up federated identity credential..."
  local federated_id_name="storage-federated-credential"
  local fed_id_exists=$(az identity federated-credential list \
    --identity-name $identity_name \
    --resource-group $rg_name \
    --query "[?name=='$federated_id_name'].name" -o tsv 2>/dev/null || echo "")

  if [[ -z "$fed_id_exists" ]]; then
    log "Creating federated identity credential..."
    az identity federated-credential create \
      --name $federated_id_name \
      --identity-name $identity_name \
      --resource-group $rg_name \
      --issuer $oidc_issuer \
      --subject "system:serviceaccount:${sa_namespace}:${sa_name}" \
      --audience "api://AzureADTokenExchange" \
      --output none
    log "Federated identity credential created successfully."
  else
    log "Federated identity credential already exists."
  fi

  log "Workload identity configured successfully."
}

# --------------------------
# STORAGE CONFIGURATION FUNCTIONS
# --------------------------

# create_dynamic_file_storage()
# ------------------------
# Sets up dynamic provisioning by creating a StorageClass and PVC.
# Note: Dynamic provisioning here uses storage keys.
# Parameters:
#   $1 - Kubernetes namespace to deploy PVC.
create_dynamic_file_storage() {
  local namespace=$1

  verify "$namespace" "create_dynamic_file_storage-ERROR: Argument (NAMESPACE) not received"

  trace "Verifying dynamic storage class existence..."
  local sc_exists=$(kubectl get storageclass dynamic-storage 2>/dev/null || echo "")

  if [[ -z "$sc_exists" ]]; then
    log "Creating dynamic storage class (key-based provisioning)..."
    process_template "templates/file/dynamic-storageclass.yaml" "dynamic-storageclass-processed.yaml"
    kubectl apply -f dynamic-storageclass-processed.yaml
    rm dynamic-storageclass-processed.yaml
    log "Dynamic storage class created."
  else
    log "Dynamic storage class already exists."
  fi

  trace "Checking for dynamic PVC..."
  local pvc_exists=$(kubectl get pvc -n $namespace dynamic-storage-pvc 2>/dev/null || echo "")

  if [[ -z "$pvc_exists" ]]; then
    log "Creating dynamic PVC..."
    process_template "templates/file/dynamic-pvc.yaml" "dynamic-pvc-processed.yaml" \
      "NAMESPACE=$namespace"
    kubectl apply -f dynamic-pvc-processed.yaml
    rm dynamic-pvc-processed.yaml
    log "Dynamic PVC created."
  else
    log "Dynamic PVC already exists."
  fi

  log "Dynamic storage configuration completed."
}

# create_static_file_storage()
# ------------------------
# Creates a static PersistentVolume (PV) and PersistentVolumeClaim (PVC) for Azure Files,
# using workload identity authentication (no storage keys required).
# Parameters:
#   $1 - Namespace.
#   $2 - Resource group name.
#   $3 - Storage account name.
#   $4 - File share name.
#   $5 - Managed identity client ID.
#   $6 - Node resource group.
create_static_file_storage() {
  local namespace=$1
  local rg_name=$2
  local storage_name=$3
  local file_share_name=$4
  local client_id=$5
  local node_rg=$6

  verify "$namespace" "create_static_file_storage-ERROR: Argument (NAMESPACE) not received"
  verify "$rg_name" "create_static_file_storage-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$storage_name" "create_static_file_storage-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$file_share_name" "create_static_file_storage-ERROR: Argument (FILE_SHARE_NAME) not received"
  verify "$client_id" "create_static_file_storage-ERROR: Argument (CLIENT_ID) not received"
  verify "$node_rg" "create_static_file_storage-ERROR: Argument (NODE_RESOURCE_GROUP) not received"

  # Get tenant ID and subscription ID
  local tenant_id=$(az account show --query tenantId -o tsv)
  local subscription_id=$(az account show --query id -o tsv)
  local volume_handle="${rg_name}#${storage_name}#${file_share_name}"

  trace "Verifying if static PV exists..."
  local pv_exists=$(kubectl get pv azurefile-pv 2>/dev/null || echo "")

  if [[ -n "$pv_exists" ]]; then
    log "Deleting existing static PV for update..."
    kubectl delete pv azurefile-pv
    sleep 5
  fi

  log "Creating static PV with workload identity configuration..."
  process_template "templates/file/static-pv.yaml" "static-pv-processed.yaml" \
    "VOLUME_HANDLE=$volume_handle" \
    "STORAGE_ACCOUNT=$storage_name" \
    "FILE_SHARE_NAME=$file_share_name" \
    "CLIENT_ID=$client_id" \
    "RESOURCE_GROUP=$rg_name"
  kubectl apply -f static-pv-processed.yaml
  rm static-pv-processed.yaml

  trace "Checking if static PVC exists..."
  local pvc_exists=$(kubectl get pvc -n $namespace pvc-azurefile 2>/dev/null || echo "")

  if [[ -n "$pvc_exists" ]]; then
    log "Deleting existing static PVC for update..."
    kubectl delete pvc -n $namespace pvc-azurefile
    sleep 5
  fi

  log "Creating static PVC..."
  process_template "templates/file/static-pvc.yaml" "static-pvc-processed.yaml" \
    "NAMESPACE=$namespace"
  kubectl apply -f static-pvc-processed.yaml
  rm static-pvc-processed.yaml

  log "Static storage configuration completed."
}

# Update dynamic blob storage provisioning to use the built-in storage class.
create_dynamic_blob_storage() {
  local namespace=$1
  local storage_name=$2
  local rg_name=$3

  verify "$namespace" "create_dynamic_blob_storage-ERROR: Argument (NAMESPACE) not received"
  verify "$storage_name" "create_dynamic_blob_storage-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$rg_name" "create_dynamic_blob_storage-ERROR: Argument (RESOURCE_GROUP) not received"

  # Skip custom StorageClass creation. AKS now provides azureblob-nfs-premium.
  log "Using built-in storage class 'azureblob-nfs-premium' for dynamic blob provisioning."

  trace "Checking for dynamic blob PVC existence..."
  local pvc_exists=$(kubectl get pvc -n $namespace dynamic-blob-pvc 2>/dev/null || echo "")

  if [[ -n "$pvc_exists" ]]; then
    log "Deleting existing dynamic blob PVC for update..."
    kubectl delete pvc -n $namespace dynamic-blob-pvc
    sleep 5
  fi

  log "Creating dynamic blob PVC with built-in storage class..."
  process_template "templates/blob/dynamic-pvc.yaml" "dynamic-blob-pvc-processed.yaml" \
    "NAMESPACE=$namespace"
  kubectl apply -f dynamic-blob-pvc-processed.yaml
  rm dynamic-blob-pvc-processed.yaml

  trace "Waiting for dynamic blob PVC to become bound..."
  sleep 30
  log "Dynamic blob storage configuration completed."
}

# Update static blob storage provisioning to use FUSE instead of NFS for blob storage.
create_static_blob_storage() {
  local namespace=$1
  local rg_name=$2
  local storage_name=$3
  local client_id=$4

  verify "$namespace" "create_static_blob_storage-ERROR: Argument (NAMESPACE) not received"
  verify "$rg_name" "create_static_blob_storage-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$storage_name" "create_static_blob_storage-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$client_id" "create_static_blob_storage-ERROR: Argument (CLIENT_ID) not received"

  # Delete previous PV if exists
  trace "Checking for existing static blob PV..."
  local pv_exists=$(kubectl get pv blob-static-pv 2>/dev/null || echo "")
  if [[ -n "$pv_exists" ]]; then
    log "Deleting existing static blob PV for update..."
    kubectl delete pv blob-static-pv
    sleep 5
  fi

  log "Creating static blob PV using FUSE..."
  process_template "templates/blob/static-pv.yaml" "static-blob-pv-processed.yaml" \
    "STORAGE_ACCOUNT=$storage_name" \
    "CONTAINER_NAME=blob-container" \
    "RESOURCE_GROUP=$rg_name" \
    "CLIENT_ID=$client_id"
  kubectl apply -f static-blob-pv-processed.yaml
  rm static-blob-pv-processed.yaml

  trace "Checking if static PVC exists..."
  local pvc_exists=$(kubectl get pvc -n $namespace pvc-blob 2>/dev/null || echo "")

  if [[ -n "$pvc_exists" ]]; then
    log "Deleting existing static blob PVC for update..."
    kubectl delete pvc -n $namespace pvc-blob
    sleep 5
  fi

  log "Creating static blob PVC..."
  process_template "templates/blob/static-pvc.yaml" "static-blob-pvc-processed.yaml" \
    "NAMESPACE=$namespace"
  kubectl apply -f static-blob-pvc-processed.yaml
  rm static-blob-pvc-processed.yaml

  trace "Waiting for static blob PVC to become bound..."
  sleep 30
  log "Static blob storage configuration completed."
}

# --------------------------
# VALIDATION FUNCTIONS (unchanged)
# --------------------------
create_validation_file_dynamic_job() {
  local namespace=$1
  local sa_name=$2
  local storage_name=$3
  local file_share_name=$4

  verify "$namespace" "create_validation_file_dynamic_job-ERROR: Argument (NAMESPACE) not received"
  verify "$sa_name" "create_validation_file_dynamic_job-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"
  verify "$storage_name" "create_validation_file_dynamic_job-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$file_share_name" "create_validation_file_dynamic_job-ERROR: Argument (FILE_SHARE_NAME) not received"

  log "Creating dynamic storage validation ConfigMap..."
  process_template "templates/configmap.yaml" "file-dynamic-configmap-processed.yaml" \
    "NAME=dynamic-file-upload-script" \
    "NAMESPACE=$namespace" \
    "STORAGE_TYPE=FILE" \
    "MOUNT_PATH=share"
  kubectl apply -f file-dynamic-configmap-processed.yaml
  rm file-dynamic-configmap-processed.yaml

  log "Creating dynamic storage validation job..."
  kubectl delete job -n $namespace dynamic-file-creator 2>/dev/null || true

  process_template "templates/job.yaml" "file-dynamic-job-processed.yaml" \
    "NAME=dynamic-file-creator" \
    "NAMESPACE=$namespace" \
    "SERVICE_ACCOUNT=$sa_name" \
    "CONFIGMAP_NAME=dynamic-file-upload-script" \
    "PVC_NAME=dynamic-storage-pvc" \
    "STORAGE_TYPE=FILE" \
    "MOUNT_PATH=share"
  kubectl apply -f file-dynamic-job-processed.yaml
  rm file-dynamic-job-processed.yaml

  trace "Waiting for dynamic storage job to complete..."
  kubectl wait --for=condition=complete job/dynamic-file-creator -n $namespace --timeout=80s

  if [ $? -eq 0 ]; then
    log "Dynamic storage job completed successfully."
    kubectl logs job/dynamic-file-creator -n $namespace
  else
    warn "Dynamic storage job did not complete within the expected time."
  fi
}

create_validation_file_static_job() {
  local namespace=$1
  local sa_name=$2
  local storage_name=$3
  local file_share_name=$4

  verify "$namespace" "create_validation_file_static_job-ERROR: Argument (NAMESPACE) not received"
  verify "$sa_name" "create_validation_file_static_job-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"
  verify "$storage_name" "create_validation_file_static_job-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$file_share_name" "create_validation_file_static_job-ERROR: Argument (FILE_SHARE_NAME) not received"

  log "Creating static storage validation ConfigMap..."
  process_template "templates/configmap.yaml" "file-static-configmap-processed.yaml" \
    "NAME=static-file-upload-script" \
    "NAMESPACE=$namespace" \
    "STORAGE_TYPE=FILE" \
    "MOUNT_PATH=static"
  kubectl apply -f file-static-configmap-processed.yaml
  rm file-static-configmap-processed.yaml

  log "Creating static storage validation job..."
  kubectl delete job -n $namespace static-file-creator 2>/dev/null || true

  process_template "templates/job.yaml" "file-static-job-processed.yaml" \
    "NAME=static-file-creator" \
    "NAMESPACE=$namespace" \
    "SERVICE_ACCOUNT=$sa_name" \
    "CONFIGMAP_NAME=static-file-upload-script" \
    "PVC_NAME=pvc-azurefile" \
    "STORAGE_TYPE=FILE" \
    "MOUNT_PATH=static"
  kubectl apply -f file-static-job-processed.yaml
  rm file-static-job-processed.yaml

  trace "Waiting for static storage job to complete..."
  kubectl wait --for=condition=complete job/static-file-creator -n $namespace --timeout=60s

  if [ $? -eq 0 ]; then
    log "Static storage job completed successfully."
    kubectl logs job/static-file-creator -n $namespace
  else
    warn "Static storage job did not complete within the expected time."
  fi
}

create_validation_blob_dynamic_job() {
  local namespace=$1
  local sa_name=$2
  local storage_name=$3

  verify "$namespace" "create_validation_blob_dynamic_job-ERROR: Argument (NAMESPACE) not received"
  verify "$sa_name" "create_validation_blob_dynamic_job-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"
  verify "$storage_name" "create_validation_blob_dynamic_job-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"

  log "Creating dynamic blob storage validation ConfigMap..."
  process_template "templates/configmap.yaml" "blob-dynamic-configmap-processed.yaml" \
    "NAME=dynamic-blob-upload-script" \
    "NAMESPACE=$namespace" \
    "STORAGE_TYPE=BLOB" \
    "MOUNT_PATH=dynamic-blob"
  kubectl apply -f blob-dynamic-configmap-processed.yaml
  rm blob-dynamic-configmap-processed.yaml

  log "Creating dynamic blob storage validation job..."
  kubectl delete job -n $namespace dynamic-blob-creator 2>/dev/null || true

  process_template "templates/job.yaml" "blob-dynamic-job-processed.yaml" \
    "NAME=dynamic-blob-creator" \
    "NAMESPACE=$namespace" \
    "SERVICE_ACCOUNT=$sa_name" \
    "CONFIGMAP_NAME=dynamic-blob-upload-script" \
    "PVC_NAME=dynamic-blob-pvc" \
    "STORAGE_TYPE=BLOB" \
    "MOUNT_PATH=dynamic-blob"
  kubectl apply -f blob-dynamic-job-processed.yaml
  rm blob-dynamic-job-processed.yaml

  trace "Waiting for dynamic blob storage job to complete..."
  kubectl wait --for=condition=complete job/dynamic-blob-creator -n $namespace --timeout=80s

  if [ $? -eq 0 ]; then
    log "Dynamic blob storage job completed successfully."
    kubectl logs job/dynamic-blob-creator -n $namespace
  else
    warn "Dynamic blob storage job did not complete within the expected time."
  fi
}

create_validation_blob_static_job() {
  local namespace=$1
  local sa_name=$2
  local storage_name=$3

  verify "$namespace" "create_validation_blob_static_job-ERROR: Argument (NAMESPACE) not received"
  verify "$sa_name" "create_validation_blob_static_job-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"
  verify "$storage_name" "create_validation_blob_static_job-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"

  log "Creating static blob storage validation ConfigMap..."
  process_template "templates/configmap.yaml" "blob-static-configmap-processed.yaml" \
    "NAME=static-blob-upload-script" \
    "NAMESPACE=$namespace" \
    "STORAGE_TYPE=BLOB" \
    "MOUNT_PATH=static-blob"
  kubectl apply -f blob-static-configmap-processed.yaml
  rm blob-static-configmap-processed.yaml

  log "Creating static blob storage validation job..."
  kubectl delete job -n $namespace static-blob-creator 2>/dev/null || true

  process_template "templates/job.yaml" "blob-static-job-processed.yaml" \
    "NAME=static-blob-creator" \
    "NAMESPACE=$namespace" \
    "SERVICE_ACCOUNT=$sa_name" \
    "CONFIGMAP_NAME=static-blob-upload-script" \
    "PVC_NAME=pvc-blob" \
    "STORAGE_TYPE=BLOB" \
    "MOUNT_PATH=static-blob"
  kubectl apply -f blob-static-job-processed.yaml
  rm blob-static-job-processed.yaml

  trace "Waiting for static blob storage job to complete..."
  kubectl wait --for=condition=complete job/static-blob-creator -n $namespace --timeout=60s

  if [ $? -eq 0 ]; then
    log "Static blob storage job completed successfully."
    kubectl logs job/static-blob-creator -n $namespace
  else
    warn "Static blob storage job did not complete within the expected time."
  fi
}

validate_storage() {
  local namespace=$1
  local sa_name=$2

  verify "$namespace" "validate_storage-ERROR: Argument (NAMESPACE) not received"
  verify "$sa_name" "validate_storage-ERROR: Argument (SERVICE_ACCOUNT_NAME) not received"

  log "Creating validation pod to verify files in storage shares..."
  kubectl delete pod storage-validation-pod -n $namespace 2>/dev/null || true

  process_template "templates/validation-pod.yaml" "validation-pod-processed.yaml" \
    "NAMESPACE=$namespace" \
    "SERVICE_ACCOUNT=$sa_name"
  kubectl apply -f validation-pod-processed.yaml
  rm validation-pod-processed.yaml

  trace "Waiting for validation pod to become ready..."
  sleep 5
  for i in {1..12}; do
    status=$(kubectl get pod storage-validation-pod -n $namespace -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [[ "$status" == "Succeeded" || "$status" == "Failed" ]]; then
      log "Validation pod completed with status: $status"
      break
    fi
    echo -n "."
    sleep 5
  done
  
  sleep 2

  kubectl logs storage-validation-pod -n $namespace

  local exit_code=$(kubectl get pod storage-validation-pod -n $namespace -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || echo "")

  case "$exit_code" in
    "0")
      log "Validation successful! Both storage types are working correctly."
      ;;
    "1")
      warn "Partial validation: Only dynamic storage is working correctly."
      ;;
    "2")
      warn "Partial validation: Only static storage is working correctly."
      ;;
    "3")
      error "Validation failed: Neither storage type passed the test."
      ;;
    *)
      warn "Validation status unknown. Please check logs manually."
      ;;
  esac

  kubectl delete pod storage-validation-pod -n $namespace
}

# --------------------------
# INFORMATION DISPLAY
# --------------------------
display_information() {
  local rg_name=$1
  local aks_name=$2
  local storage_name=$3
  local file_share_name=$4
  local identity_name=$5
  local identity_client_id=$6

  verify "$rg_name" "display_information-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$aks_name" "display_information-ERROR: Argument (AKS_NAME) not received"
  verify "$storage_name" "display_information-ERROR: Argument (STORAGE_ACCOUNT_NAME) not received"
  verify "$file_share_name" "display_information-ERROR: Argument (FILE_SHARE_NAME) not received"
  verify "$identity_name" "display_information-ERROR: Argument (IDENTITY_NAME) not received"
  verify "$identity_client_id" "display_information-ERROR: Argument (IDENTITY_CLIENT_ID) not received"

  echo ""
  echo -e "${GREEN}=== Setup Complete ===${NC}"
  echo ""
  echo -e "${YELLOW}Created Resources:${NC}"
  echo "Resource Group: $rg_name"
  echo "Location: $LOCATION"
  echo "AKS Cluster: $aks_name"
  echo "Storage Account: $storage_name"
  echo "File Share: $file_share_name"
  echo "Identity Name: $identity_name"
  echo "Identity Client ID: $identity_client_id"
  echo ""
  echo -e "${YELLOW}Useful Commands:${NC}"
  echo "# To connect to the AKS cluster:"
  echo "az aks get-credentials --resource-group $rg_name --name $aks_name --overwrite-existing"
  echo ""
  echo "# To verify the storage file in Azure Files:"
  echo "az storage file list --account-name $storage_name --share-name $file_share_name --auth-mode login --output table"
  echo ""
  echo -e "${YELLOW}Validation Results:${NC}"
  echo "1. Workload Identity successfully configured"
  echo "2. Static PV with Workload Identity is functioning correctly"
  echo ""
  echo -e "${YELLOW}Key Findings:${NC}"
  echo "1. Dynamic Provisioning: Uses built-in storage class (azureblob-nfs-premium)"
  echo "2. Static Provisioning: Uses built-in storage class (azureblob-nfs-premium) with manual PV definition"
  echo ""
  echo -e "${YELLOW}Cleanup Instructions:${NC}"
  echo "To delete all resources when testing is complete:"
  echo "  az group delete --name $rg_name --yes"
  echo ""
}

# --------------------------
# HELP FUNCTION
# --------------------------
show_help() {
  echo "Usage: $0 [unique-id]"
  echo ""
  echo "Arguments:"
  echo "  unique-id      Optional alphanumeric ID for resource naming."
  echo "                 If not provided, a random ID will be generated."
  echo ""
  echo "Options:"
  echo "  -h, --help     Show this help message."
  echo ""
  echo "Examples:"
  echo "  $0             Run with a generated unique ID."
  echo "  $0 abc123      Run with the specific unique ID 'abc123'."
  echo ""
}

# --------------------------
# MAIN FUNCTION
# --------------------------
main() {
  echo -e "${GREEN}=== Breaking Free from Keys: AKS and Azure Storage Integration PoC ===${NC}"
  echo ""

  UNIQUE_ID=""
  if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    show_help
    exit 0
  elif [[ -n "$1" ]]; then
    UNIQUE_ID="$1"
  fi

  generate_config "$UNIQUE_ID"

  print_section_header "1. Prerequisites & Configuration"
  check_prerequisites

  print_section_header "2. Azure Resource Creation"
  create_resource_group "$RESOURCE_GROUP" "$LOCATION"
  create_managed_identity "$RESOURCE_GROUP" "$IDENTITY_NAME" "$LOCATION"
  create_storage_account "$RESOURCE_GROUP" "$STORAGE_ACCOUNT_NAME" "$LOCATION" "$FILE_SHARE_NAME" "$IDENTITY_PRINCIPAL_ID"
  create_aks_cluster "$RESOURCE_GROUP" "$AKS_NAME" "$LOCATION"

  print_section_header "3. Role Assignments"
  assign_all_roles "$RESOURCE_GROUP" "$IDENTITY_PRINCIPAL_ID" "$STORAGE_ACCOUNT_ID" "$NODE_RESOURCE_GROUP"

  print_section_header "4. Workload Identity Configuration"
  configure_workload_identity "$RESOURCE_GROUP" "$IDENTITY_NAME" "$IDENTITY_CLIENT_ID" "$AKS_OIDC_ISSUER" "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME"

  print_section_header "5. Storage Provisioning"
  # Azure Files
  create_dynamic_file_storage "$SERVICE_ACCOUNT_NAMESPACE"
  create_static_file_storage "$SERVICE_ACCOUNT_NAMESPACE" "$RESOURCE_GROUP" "$STORAGE_ACCOUNT_NAME" "$FILE_SHARE_NAME" "$IDENTITY_CLIENT_ID" "$NODE_RESOURCE_GROUP"

  # Azure Blobs using built-in storage classes
  create_dynamic_blob_storage "$SERVICE_ACCOUNT_NAMESPACE" "$STORAGE_ACCOUNT_NAME" "$RESOURCE_GROUP"
  create_static_blob_storage "$SERVICE_ACCOUNT_NAMESPACE" "$RESOURCE_GROUP" "$STORAGE_ACCOUNT_NAME" "$IDENTITY_CLIENT_ID"

  print_section_header "5. Validation Jobs"
  create_validation_file_dynamic_job "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$STORAGE_ACCOUNT_NAME" "$FILE_SHARE_NAME"
  create_validation_file_static_job "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$STORAGE_ACCOUNT_NAME" "$FILE_SHARE_NAME"
  create_validation_blob_dynamic_job "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$STORAGE_ACCOUNT_NAME"
  create_validation_blob_static_job "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$STORAGE_ACCOUNT_NAME"
  validate_storage "$SERVICE_ACCOUNT_NAMESPACE" "$SERVICE_ACCOUNT_NAME"

  print_section_header "6. Final Information & Summary"
  display_information "$RESOURCE_GROUP" "$AKS_NAME" "$STORAGE_ACCOUNT_NAME" "$FILE_SHARE_NAME" "$IDENTITY_NAME" "$IDENTITY_CLIENT_ID"
}

# Run the script
main "$@"
