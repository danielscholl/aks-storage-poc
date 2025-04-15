# Breaking Free from Keys: AKS & Azure Storage Integration PoC

## 1. Overview

This document outlines the requirements for a Proof of Concept (PoC) demonstrating the integration of Azure Storage services with Azure Kubernetes Service (AKS). The PoC will showcase both Azure Files and Azure Blob Storage integration patterns, demonstrating how containerized applications can leverage different types of persistent storage within Kubernetes based on their specific needs.

## 2. Goals

Primary Goal:
* Investigate and document Azure Storage integration patterns with AKS that can operate securely with shared key access disabled (`--allow-shared-key-access false`)
* Identify which storage types and provisioning methods support keyless access through managed identities and workload identity federation

Secondary Goals:
* Demonstrate and evaluate four distinct storage integration patterns:
    1. Dynamic Provisioning (Kubernetes-initiated-creation) of Azure Files
        - Document whether it supports disabled shared key access
        - Validate full functionality with both key and keyless access attempts
    2. Dynamic Provisioning (Kubernetes-initiated-creation) of Azure Blob Storage
        - Document whether it supports disabled shared key access
        - Test different CSI driver modes (FUSE, NFS) for keyless access
    3. Static Provisioning (arm-initiated-creation) of Azure Files
        - Document whether it supports disabled shared key access
        - Configure with proper workload identity federation
    4. Static Provisioning (arm-initiated-creation) of Azure Blob Storage
        - Document whether it supports disabled shared key access
        - Configure with proper workload identity federation

Success Metrics:
* Each storage pattern must be successfully implemented and tested
* Each pattern must be clearly documented as either:
    - Supporting disabled shared key access
    - Requiring shared key access
* All patterns must demonstrate working storage access regardless of shared key setting
* Implementation must use managed identities where possible
* Setup process must be automated and reproducible
* Document any limitations and issues discovered during implementation

## 3. Scope

*   **In Scope:**
    *   Phase 1 - Establish Working Baseline:
        - Create all Azure resources with default security settings
        - Implement and validate all storage patterns:
            * Dynamic Azure Files provisioning
            * Static Azure Files provisioning
            * Dynamic Azure Blob provisioning
            * Static Azure Blob provisioning
        - Implement and validate identity configurations:
            * Workload Identity federation
            * Service Account mappings
            * Managed Identity access
        - Document successful configurations and access patterns

    *   Phase 2 - Security Enhancement Testing:
        - Disable shared key access on validated storage account
        - Retest all working configurations from Phase 1
        - Document which configurations:
            * Continue to work without shared key access
            * Fail when shared key access is disabled
            * Require additional configuration changes
        - Identify any workarounds or alternatives for failed configurations
        - Test different storage driver options (azureblob-fuse-premium, azureblob-nfs-premium)

    *   Automation and Documentation:
        - Script to support both phases of testing
        - Clear documentation of both working baseline and security impact
        - Migration guidance for moving from Phase 1 to Phase 2 configurations
*   **Out of Scope:**
    *   Production-level hardening, monitoring, or complex security configurations
    *   Performance benchmarking of storage solutions
    *   Advanced storage features (e.g., snapshots, private endpoints beyond basic setup)
    *   Detailed application deployment beyond the validation Jobs
    *   Automated teardown script (unless explicitly added to `setup.sh` requirements)

## 4. Core Components

*   **Azure Infrastructure:**
    *   Azure Resource Group: To contain all PoC resources
    *   Azure Managed Identity (UAMI): For AKS and CSI drivers authentication to Azure Storage
    *   Azure Storage Account: To host both Azure Files shares and Blob containers
    *   Azure Kubernetes Service (AKS): The managed Kubernetes cluster environment with workload identity enabled
*   **Kubernetes Objects:**
    *   Identity-related:
        *   `ServiceAccount`: For workload identity federation
        *   Federated identity credential: To link service account with managed identity
    *   Files-related:
        *   `StorageClass`: For dynamic provisioning using azurefile-csi driver
        *   `PersistentVolume` (PV): For static provisioning with pre-created file share
        *   `PersistentVolumeClaim` (PVC): For storage requests
    *   Blob-related:
        *   `StorageClass`: For dynamic provisioning with different storage options (FUSE or NFS)
        *   `PersistentVolume` (PV): For static provisioning with pre-created blob container
        *   `PersistentVolumeClaim` (PVC): For storage requests
    *   Testing-related:
        *   `ConfigMap`: For validation scripts
        *   `Job`: For validation testing
        *   `Pod`: For comprehensive validation across storage types

*   **Automation:**
    *   `setup.sh`: Shell script automating Azure resource creation and Kubernetes object deployment
*   **Documentation:**
    *   `poc.md`: Step-by-step guide explaining the PoC and documenting findings

## 5. Use Cases

### Use Case 1: Dynamic Provisioning with Azure Files StorageClass

*   **Goal:** Demonstrate creating persistent storage on-demand using Azure Files without pre-provisioning the storage account or file share.
*   **Mechanism:** Define a `StorageClass` using the `file.csi.azure.com` provisioner. Create a `PersistentVolumeClaim` referencing this `StorageClass`. The CSI driver automatically provisions an Azure Files share and binds it to the PVC.
*   **Expected Outcome:** Document whether dynamic provisioning works with keyless access using workload identity.
*   **Example Manifests:** (See `specs/copilot_poc_instructions.md` for specific YAML)
    *   `StorageClass` (`dynamic-storage`)
    *   `PersistentVolumeClaim` (`storage-pvc`)

### Use Case 2: Static Provisioning with Pre-configured Azure Files Share

*   **Goal:** Demonstrate using a pre-existing Azure Files share within Kubernetes.
*   **Mechanism:** Manually create an Azure Files share. Define a `PersistentVolume` that points to this existing share, including necessary credentials/references (Storage Account, Share Name, Resource Group, Managed Identity Client ID, Tenant ID). Create a `PersistentVolumeClaim` that binds to this specific PV.
*   **Expected Outcome:** Document whether static provisioning works with keyless access using workload identity.
*   **Example Manifests:** (See `specs/copilot_poc_instructions.md` for specific YAML)
    *   `PersistentVolume` (`azurefile-pv`)
    *   `PersistentVolumeClaim` (`azurefile-pvc`)

### Use Case 3: Azure Blob Storage Container Mounting

*   **Goal:** Demonstrate mounting Azure Blob containers in Kubernetes pods
*   **Requirements:**
    - Must support dynamic provisioning of blob containers
    - Must allow ReadWriteMany access mode
    - Must support standard storage tier
    - Must integrate with the cluster's managed identity for authentication
    - Must allow pods to mount the blob storage as a volume
*   **Expected Outcome:** Test and document different mounting options (FUSE vs NFS) and their compatibility with keyless access.

### Use Case 4: Static Provisioning with Pre-configured Azure Blob Container

*   **Goal:** Demonstrate using a pre-existing Blob container within Kubernetes
*   **Requirements:**
    - Create persistent volume pointing to existing container
    - Configure with workload identity
    - Test with both FUSE and NFS mount options
*   **Expected Outcome:** Document which options support keyless access and any limitations discovered

## 6. Functional Requirements

### 6.1. Infrastructure Setup (`setup.sh`)

*   The script MUST accept configuration variables (e.g., Resource Group name, Location, Storage Account name, AKS cluster name).
*   The script MUST check for prerequisites (e.g., Azure CLI login, required tools).
*   The script MUST create the necessary Azure Resource Group.
*   The script MUST create the Azure Storage Account.
*   The script MUST create the Azure Managed Identity (UAMI).
*   The script MUST create the AKS cluster, configured with:
    - Workload Identity enabled
    - OIDC issuer enabled
    - Azure Files CSI driver enabled
    - Azure Blob Storage CSI driver enabled
*   The script MUST grant the necessary permissions for the UAMI on the Storage Account:
    - Storage Blob Data Contributor
    - Storage File Data SMB Share Contributor
    - Storage Account Key Operator Service Role (if needed)
*   The script MUST create federated identity credentials linking service accounts to managed identity
*   The script should output relevant IDs and names for reference

### 6.2. Kubernetes Configuration

*   Kubernetes manifests for all required objects (StorageClass, PV, PVC, ConfigMap, Job, SA) MUST be created in the `templates/` directory.
*   The `setup.sh` script SHOULD apply these manifests to the AKS cluster *or* instruct the user on how to apply them.
*   Template manifests MUST support variable substitution for dynamic values
*   Kubernetes manifests for both Files and Blob storage scenarios MUST be provided in their respective directories (`templates/file/` and `templates/blob/`)
*   Each storage type MUST have its own validation Job and ConfigMap
*   The script MUST configure service accounts with proper workload identity annotations

### 6.3. Automation Script (`setup.sh`)

*   MUST contain sections for Configuration, Utility Functions, Prerequisite Checks, and Resource Creation steps.
*   SHOULD provide clear output messages indicating progress and success/failure of steps.
*   SHOULD be idempotent where possible, or provide guidance on cleanup/rerunning.
*   MUST include error handling and validation steps
*   SHOULD clearly distinguish between phases of the PoC with section headers

### 6.4. PoC Documentation (`poc.md`)

*   MUST follow the structure and style of `ai-docs/sample_poc.md`.
*   MUST include sections for Prerequisites, Core Components, and Step-by-Step Implementation.
*   MUST clearly explain how to configure and run the `setup.sh` script.
*   MUST detail the steps for deploying Kubernetes objects (if not fully automated by `setup.sh`).
*   MUST explain the validation steps.
*   MUST include separate sections for Azure Files and Blob Storage implementations
*   MUST provide comparison guidance on when to use each storage type
*   MUST document the findings about keyless access support for each storage type and provisioning method
*   MUST highlight any known issues or limitations discovered

## 7. Validation

### 7.1 Azure Files Validation

*   A Kubernetes `ConfigMap` (`file-upload-script`) will store a simple shell script (`init.sh`).
*   The `init.sh` script will write a "Hello World" type message, including the current date/time, to a file (e.g., `/share/hello-world.txt`) within the mounted volume.
*   A Kubernetes `Job` (`hello-world-file-creator`) will mount the PVC (e.g., `hello-world-pvc` - *Note: PVC name needs consistency across examples*) and the ConfigMap script volume.
*   The Job will execute the `init.sh` script.
*   **Success Criteria:**
    1.  The Kubernetes Job completes successfully.
    2.  Verification (manual or scripted via Azure CLI) confirms the existence and content of the `hello-world.txt` file directly within the corresponding Azure Files share.
    ```bash
    # Example Azure CLI validation command (placeholders needed)
    az storage file download --account-name <STORAGE_ACCOUNT> --share-name <FILESHARE_NAME> --path "hello-world.txt" --file-path "downloaded-hello-world.txt" [--account-key <STORAGE_KEY> | --sas-token <SAS_TOKEN> | --auth-mode login]
    cat downloaded-hello-world.txt
    ```

### 7.2 Azure Blob Storage Validation

*   A separate Kubernetes `ConfigMap` (`blob-upload-script`) will store a validation script
*   The script will create a test blob with timestamp data
*   A Kubernetes `Job` (`blob-creator`) will mount the Blob PVC and execute the validation
*   **Success Criteria:**
    1.  The Kubernetes Job completes successfully
    2.  Verification confirms the blob exists in the container:
    ```bash
    # Example Azure CLI validation command (placeholders needed)
    az storage blob download --account-name <STORAGE_ACCOUNT> --container-name <CONTAINER_NAME> --name "test-blob.txt" --file "downloaded-blob.txt" [--auth-mode login]
    cat downloaded-blob.txt
    ```

### 7.3 Comprehensive Validation

*   A validation pod that mounts all storage types simultaneously
*   Verify access to all storage types from a single pod
*   Test both writing and reading operations
*   Document any failure scenarios or limitations encountered 