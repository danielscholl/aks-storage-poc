# Breaking Free from Keys

**Secure AKS and Azure Storage Integration Without Secrets**

> Kubernetes teams adopting Azure face a critical challenge: `securely accessing storage without violating security standards`.

## Purpose

This PoC attempts to address critical limitations in keyless Azure Storage access within AKS, offering a practical path forward. The goal is to enable organizations to align AKS storage strategies with modern security practices, particularly zero-trust and least-privilege principles, while reducing operational friction.

## Choose Your Path

| Role / Focus | Start Here |
|--------------|------------|
| 🧑‍💼 Executive | [Executive Summary](docs/executive_summary.md) → Why keyless access matters and Microsoft gaps |
| 🧑‍💻 Technical | [Technical Summary](docs/technical_overview.md) → Deep-dive on the drivers and provisioning models |

## The Proof of Concept

Organizations have a critical need for keyless access to Azure Storage to meet compliance and enhance operational security. By searching for existing gaps in Microsoft's implementation, this PoC offers practical guidance and the ability to test different configurations. 

The provided tool enables users to explore and deploy various configurations for integrating Azure Kubernetes Service (AKS) with Azure Storage. It supports combinations of persistent or dynamic storage, blob or file storage, and options to enable or disable shared key capabilities. The goal is to understand the configuations and see how they actually work. The following sections provide a detailed, step-by-step guide to implementing these configurations.


## Before You Begin

To deploy this PoC successfully, ensure the following prerequisites are in place:

- **Azure subscription** with permission to create AKS clusters and manage identity/storage
- **Python** (version 3.11 or later)
- **uv** (installed via `pip install uv`)
- **Azure CLI** (version 2.70.0 or later)
- **kubectl** (installed via `az aks --install-cli`)

## Automated Implementation

Use the provided Python script to automate the process of deploying the desired Azure Storage integration with AKS use cases.

> For fully manual flows, refer to the [tutorial](docs/tutorial.md) or use the bash [setup.sh](scripts/setup.sh) script.

### Use Case Combinations
The script supports the following configurations:
- Blob Storage + Static Provisioning
- Blob Storage + Dynamic Provisioning
- Azure Files + Static Provisioning
- Azure Files + Dynamic Provisioning
- Blob Storage + Static Provisioning + Disabled Shared Keys

### Usage
Run the script with:

```bash
 Usage: uv run aks-storage.py [OPTIONS]
                                                                                                                                                
╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --group                     TEXT                  Group name for the settings [default: aks-storage-poc]           │
│ --storage                   [Blob|File]           Storage Type to use (Blob or File) [default: None]               │
│ --provision                 [Persistent|Dynamic]  Provision Type to use (Persistent or Dynamic) [default: None]    │
│ --disable-shared-key                              Disable shared key access on the storage account.                │
│ --help                                            Show this message and exit.                                      │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```
