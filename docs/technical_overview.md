# Technical Summary – Breaking Free from Keys

This document provides a deeper technical breakdown of the patterns, constraints, and implementation strategies used in the PoC for secure AKS + Azure Storage integration using Microsoft Entra Workload Identity.

## Purpose

To enable keyless, compliant access between Kubernetes workloads and Azure Storage (Blob and Files) without the use of storage account keys or SAS tokens, aligning with modern security best practices.

## Key Findings

The following matrix outlines what is currently supported for keyless storage access in AKS environments:

| Storage Type | Provisioning Method  | Support for Keyless Access               | Notes                                                  |
| ------------ | -------------------- | ---------------------------------------- | ------------------------------------------------------ |
| Azure Files  | Static Provisioning  | ❌ Not Supported                          | Requires `--allow-shared-key-access true`              |
| Azure Files  | Dynamic Provisioning | ❌ Not Supported                          | Requires `--allow-shared-key-access true`              |
| Azure Blob   | Static Provisioning  | ⚠️ Partial Support (OSS driver required) | Requires helm installation of the OSS Blob CSI driver. |
| Azure Blob   | Dynamic Provisioning | ❌ Not Supported                          | Requires `--allow-shared-key-access true`              |

## Architecture Overview

This architecture demonstrates a secure, keyless integration between AKS and Azure Storage, relying on Microsoft Entra Workload Identity and Azure-managed resources.

> ⚠️ **Important:** For keyless access to Azure Blob storage with workload identity, you must manually install the open-source (OSS) Blob CSI driver via Helm. The AKS-managed Blob CSI driver (`--enable-blob-driver`) does not support workload identity in keyless mode.

### Key Integration Elements

- **AKS with OIDC Issuer Enabled:** OIDC support in the AKS control plane enables identity federation for Kubernetes workloads.
- **User-Assigned Managed Identity:** Bound to a Kubernetes `ServiceAccount`, this identity authenticates to Azure without the need for storage keys or SAS tokens.
- **Federated Credential Binding:** The managed identity is federated with the AKS workload identity system to allow token-based access to Azure Blob and Files.
- **CSI Drivers:** Azure-provided CSI drivers for Blob and Files support mounting persistent volumes into Kubernetes pods. However, the AKS-managed drivers do not support keyless access via workload identity. For this capability, only the open-source Blob CSI driver—installed via Helm—can be used.
- **RBAC Enforcement:** Fine-grained Azure RBAC permissions control access to storage resources, maintaining a least-privilege security posture.

This setup eliminates the need for embedded credentials, improves compliance, and paves the way for secure automation—even if certain limitations still apply.

## Technical Objectives

This proof of concept was designed to validate the feasibility of securely integrating Azure Kubernetes Service (AKS) with Azure Storage using Microsoft Entra Workload Identity. The primary objective was to remove dependencies on storage account keys and SAS tokens by enabling token-based authentication through managed identities.

Specifically, the PoC aimed to:

- Validate secure access to Azure Blob and Azure Files from AKS without shared keys
- Explore static and dynamic provisioning capabilities using Container Storage Interface (CSI) drivers
- Identify gaps and limitations in the CSI drivers and Kubernetes `StorageClass` implementations
- Evaluate role-based access control (RBAC) requirements to enforce least privilege

## Provisioning Scenarios

Provisioning persistent storage for AKS workloads can be accomplished using either static or dynamic methods. This proof of concept tested both approaches across Azure Blob and Azure Files, with a focus on support for keyless authentication.

### Dynamic Provisioning with CSI

Dynamic provisioning using Kubernetes `StorageClass` is currently limited by Azure's lack of support for keyless account creation at runtime. Although the `azureblob-fuse-premium` driver technically supports keyless access, dynamic provisioning is only possible when the storage account has been pre-created and configured manually with shared key access disabled.

Azure Files does not support keyless access in any dynamic provisioning scenario. As a result, organizations cannot fully automate storage lifecycle management while maintaining compliance.

### Static Provisioning with Azure Blob

Static provisioning for Azure Blob storage is fully supported in a keyless configuration. However, for keyless access with workload identity, you must use the open-source Blob CSI driver installed via Helm. The process involves manual creation of a storage account and blob container, followed by Kubernetes resource definitions that reference the blob using the `azureblob-fuse-premium` CSI driver. This approach enables secure access using federated identity, without any keys or tokens.

Additionally, the `azureblob-nfs-premium` driver supports NFS-based access. However, NFS mounts do not offer in-transit encryption and require strict network access controls, making them unsuitable for many compliance-sensitive workloads.

> 🔍 **Dig Deeper:** For implementation details on static provisioning with [Azure Blob](../aks_blob_wi.md).

### Static Provisioning with Azure Files

Azure Files currently requires shared key access, even when provisioned manually. The CSI driver does not support federated identity for keyless authentication. To enable access, the storage account must be configured with `--allow-shared-key-access true`, introducing potential compliance risks.

> 🔍 **Dig Deeper:** For configuration examples and limitations of [Azure Files](../aks_file_wi.md).

### Dynamic CSI Limitations

- `azureblob-fuse-premium` supports keyless dynamic provisioning *only if* the storage account is created manually and the OSS driver is used
- Azure Files does **not** support keyless access in dynamic scenarios
- Kubernetes `StorageClass` cannot yet create accounts with `--allow-shared-key-access false`
- The AKS-managed Blob CSI driver does not support workload identity in keyless mode. Use the OSS driver for this scenario.

## Core Azure Components Used

These Azure building blocks form the foundation of the identity-based storage integration:

| Component                         | Role in Solution                          |
| --------------------------------- | ----------------------------------------- |
| AKS                               | Host for container workloads              |
| Azure Storage (Blob & Files)      | Persistent backing store                  |
| Microsoft Entra Workload Identity | Authentication to Azure resources         |
| User-Assigned Managed Identity    | Identity used by Kubernetes workload      |
| Azure RBAC                        | Authorizes access to Blob/File containers |

### Kubernetes Constructs

| Construct               | Purpose                                             |
| ----------------------- | --------------------------------------------------- |
| `ServiceAccount`        | Tied to managed identity for workload identity      |
| `PersistentVolume`      | Defines how to connect to external storage          |
| `PersistentVolumeClaim` | Application-level request for storage               |
| `Job`                   | Workload used to validate mount and access behavior |

## Known Issues and Community Discussions

> 📌 For ongoing issues and discussion related to AKS and Workload Identity with Azure Storage, see [GitHub Issue #3432](https://github.com/Azure/AKS/issues/3432).

