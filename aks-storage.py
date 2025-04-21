#!/usr/bin/env -S uv run --script

# /// script
# dependencies = [
#   "pytest>=7.4.0",
#   "rich>=13.7.0",
#   "azure-identity>=1.15.0",
#   "azure-mgmt-resource>=23.0.0",
#   "azure-mgmt-authorization>=4.0.0",
#   "azure-mgmt-storage>=21.1.0",
#   "azure-mgmt-containerservice>=27.1.0",
#   "kubernetes>=28.1.0",
#   "typer>=0.9.0",
#   "pydantic>=2.5.0",
# ]
# ///

import os
import random
import string
import shlex
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union

import pytest
import typer
from azure.identity import DefaultAzureCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.storage import StorageManagementClient
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table
from rich.box import ROUNDED
from rich.theme import Theme
import subprocess
import tempfile
import yaml
import kubernetes as k8s
from kubernetes.client import ApiClient, CoreV1Api

# Custom theme for syntax highlighting
custom_theme = Theme(
    {
        "azure": "bold cyan",
        "kubectl": "bold green",
        "info": "dim white",
        "command": "yellow",
        "success": "bold green",
        "error": "bold red",
        "warning": "bold yellow",
    }
)

# Initialize console for rich output
console = Console(theme=custom_theme)
app = typer.Typer(add_completion=False)

# Placeholder for future logging features
command_history = None


def run_command(
    cmd_list: List[str],
    capture_output: bool = True,
    text: bool = True,
    display: bool = True,
    description: str = None,
) -> subprocess.CompletedProcess:
    """
    Run a command and optionally display it to the user in a nicely formatted panel.

    Args:
        cmd_list: List of command arguments
        capture_output: Whether to capture the command output
        text: Whether to return the output as text
        display: Whether to display the command in a panel
        description: Optional description to show above the command

    Returns:
        The completed process object
    """
    # Create a formatted string with backslashes for display
    formatted_parts = []

    # First item is the command itself
    if cmd_list:
        formatted_parts.append(cmd_list[0])

    # For the rest of the items, format options with backslashes for display
    i = 1
    while i < len(cmd_list):
        # If the current item starts with "-", it's an option, add backslash before it
        if cmd_list[i].startswith("-"):
            formatted_parts.append("\\\n  " + shlex.quote(cmd_list[i]))
        else:
            # If it's a value for a previous option, add it without backslash
            formatted_parts.append(shlex.quote(cmd_list[i]))
        i += 1

    formatted_cmd = " ".join(formatted_parts)

    # For actual execution and history, we need the regular joined command string
    cmd_str = " ".join(shlex.quote(arg) for arg in cmd_list)

    # Command history removed

    # Only display the command if requested
    if display:
        # Determine command type (azure, kubectl, or other)
        style = None
        if cmd_list[0] == "az":
            style = "azure"
            title = "[azure]Azure CLI Command[/azure]"
        elif cmd_list[0] == "kubectl":
            style = "kubectl"
            title = "[kubectl]Kubernetes Command[/kubectl]"
        else:
            title = "Command"

        # Add description if provided
        if description:
            title = f"{title}: {description}"

        # Format the command with syntax highlighting
        command_syntax = Syntax(
            formatted_cmd, "bash", theme="monokai", line_numbers=False
        )

        # Display in a panel
        console.print(Panel(command_syntax, title=title, border_style=style))

    # Execute the command
    result = subprocess.run(cmd_list, capture_output=capture_output, text=text)

    # Return the result
    return result


def display_k8s_yaml(
    yaml_content: str, title: str = "Kubernetes YAML", resource_type: str = ""
):
    """Display Kubernetes YAML with syntax highlighting"""
    yaml_syntax = Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True)

    # Create a more prominent header for Kubernetes resources
    header_style = "bold cyan"
    if resource_type:
        header = f"[{header_style}]Kubernetes {resource_type}[/{header_style}]"
    else:
        header = f"[{header_style}]{title}[/{header_style}]"

    console.print(Panel(yaml_syntax, title=header, border_style="cyan", expand=False))


def display_command_result(
    result: subprocess.CompletedProcess,
    success_message: str = None,
    error_message: str = None,
    show_output: bool = True,
):
    """Display the result of a command execution"""
    if result.returncode == 0:
        if success_message:
            console.print(f"[success]✓ {success_message}[/success]")
        if show_output and result.stdout and result.stdout.strip():
            console.print(
                Panel(
                    result.stdout.strip(),
                    title="Command Output",
                    border_style="success",
                    expand=False,
                )
            )
    else:
        if error_message:
            console.print(f"[error]✗ {error_message}[/error]")
        if result.stderr and result.stderr.strip():
            console.print(
                Panel(
                    result.stderr.strip(),
                    title="Error Output",
                    border_style="error",
                    expand=False,
                )
            )
        # Exit immediately when a command fails
        console.print("[error]Execution terminated due to error[/error]")
        raise typer.Exit(code=1)


# Command history feature removed


# Define enums for command line options
class StorageType(str, Enum):
    BLOB = "Blob"
    FILE = "File"


class ProvisionType(str, Enum):
    PERSISTENT = "Persistent"
    DYNAMIC = "Dynamic"


# Define configuration model
class Config(BaseModel):
    group: str
    storage_type: StorageType
    provision_type: ProvisionType
    location: str = "centralus"
    unique_id: str = ""
    resource_group: str = ""
    storage_account: str = ""
    identity_name: str = ""
    cluster_name: str = ""
    allow_shared_key_access: Optional[bool] = (
        None  # Default to None to determine based on storage type
    )

    def __init__(self, **data):
        data.setdefault("storage_type", StorageType.BLOB)
        data.setdefault("provision_type", ProvisionType.PERSISTENT)
        super().__init__(**data)
        if not self.unique_id:
            self.unique_id = "".join(
                random.choices(string.ascii_lowercase + string.digits, k=6)
            )

        # Set derived values if not provided
        if not self.resource_group:
            self.resource_group = f"{self.group}-{self.unique_id}-rg"
        if not self.storage_account:
            # Storage account names can only be lowercase letters and numbers, max 24 chars
            sa_name = f"{self.group}{self.unique_id}sa".replace("-", "")
            self.storage_account = sa_name[:24]
        if not self.identity_name:
            self.identity_name = f"{self.group}-{self.unique_id}-identity"
        if not self.cluster_name:
            self.cluster_name = f"{self.group}-{self.unique_id}-aks"

        # If allow_shared_key_access is not explicitly set, determine it based on storage type and provision type
        if self.allow_shared_key_access is None:
            # Only Blob storage with static provisioning can use keyless access
            if (
                self.storage_type == StorageType.BLOB
                and self.provision_type == ProvisionType.PERSISTENT
            ):
                self.allow_shared_key_access = False
            else:
                # Azure Files and dynamic Blob provisioning require shared key access
                self.allow_shared_key_access = True


class AzureManager:
    def __init__(self, config: Config):
        self.config = config
        self.credential = DefaultAzureCredential()
        self.subscription_id = self._get_subscription_id()
        self.resource_client = ResourceManagementClient(
            self.credential, self.subscription_id
        )
        self.storage_client = StorageManagementClient(
            self.credential, self.subscription_id
        )
        self.auth_client = AuthorizationManagementClient(
            self.credential, self.subscription_id
        )
        self.container_client = ContainerServiceClient(
            self.credential, self.subscription_id
        )

        # Store resource IDs
        self.storage_account_id = ""
        self.identity_principal_id = ""
        self.identity_client_id = ""
        self.oidc_issuer_url = ""

        # Storage specific
        self.blob_container_name = "mycontainer"
        self.file_share_name = "myshare"

    def _get_subscription_id(self) -> str:
        """Get the current subscription ID."""
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def create_resource_group(self, all_use_cases=False, use_cases_filter=None) -> None:
        """Create the resource group.

        Args:
            all_use_cases: If True, tag the resource group with all use cases
            use_cases_filter: Optional dict with keys 'storage' and/or 'provision' to filter use cases
        """
        with console.status(
            f"Creating resource group: {self.config.resource_group}..."
        ):
            # Tag the resource group with the use cases being tested
            # This allows us to track which resource groups are used for which tests

            # Define the use cases explicitly
            use_cases = {
                "UseCase1": "Blob Storage with Static Provisioning",
                "UseCase2": "Blob Storage with Dynamic Provisioning",
                "UseCase3": "File Storage with Static Provisioning",
                "UseCase4": "File Storage with Dynamic Provisioning",
            }

            # Create a dictionary with the use cases
            tags = {}

            # For all use cases mode, add all use case tags
            if all_use_cases:
                tags = use_cases
            # For filtered use cases, add only the matching tags
            elif use_cases_filter:
                storage_filter = use_cases_filter.get("storage")
                provision_filter = use_cases_filter.get("provision")

                # Add tags based on the filters
                if (
                    storage_filter == StorageType.BLOB
                    and provision_filter == ProvisionType.PERSISTENT
                ):
                    tags["UseCase1"] = use_cases["UseCase1"]
                elif (
                    storage_filter == StorageType.BLOB
                    and provision_filter == ProvisionType.DYNAMIC
                ):
                    tags["UseCase2"] = use_cases["UseCase2"]
                elif (
                    storage_filter == StorageType.FILE
                    and provision_filter == ProvisionType.PERSISTENT
                ):
                    tags["UseCase3"] = use_cases["UseCase3"]
                elif (
                    storage_filter == StorageType.FILE
                    and provision_filter == ProvisionType.DYNAMIC
                ):
                    tags["UseCase4"] = use_cases["UseCase4"]
                elif storage_filter == StorageType.BLOB and provision_filter is None:
                    # All provision types for Blob storage
                    tags["UseCase1"] = use_cases["UseCase1"]
                    tags["UseCase2"] = use_cases["UseCase2"]
                elif storage_filter == StorageType.FILE and provision_filter is None:
                    # All provision types for File storage
                    tags["UseCase3"] = use_cases["UseCase3"]
                    tags["UseCase4"] = use_cases["UseCase4"]
                elif (
                    storage_filter is None
                    and provision_filter == ProvisionType.PERSISTENT
                ):
                    # All storage types with Persistent provisioning
                    tags["UseCase1"] = use_cases["UseCase1"]
                    tags["UseCase3"] = use_cases["UseCase3"]
                elif (
                    storage_filter is None and provision_filter == ProvisionType.DYNAMIC
                ):
                    # All storage types with Dynamic provisioning
                    tags["UseCase2"] = use_cases["UseCase2"]
                    tags["UseCase4"] = use_cases["UseCase4"]
            # Otherwise, tag based on the specific use case
            elif (
                self.config.storage_type == StorageType.BLOB
                and self.config.provision_type == ProvisionType.PERSISTENT
            ):
                tags["UseCase1"] = use_cases["UseCase1"]
            elif (
                self.config.storage_type == StorageType.BLOB
                and self.config.provision_type == ProvisionType.DYNAMIC
            ):
                tags["UseCase2"] = use_cases["UseCase2"]
            elif (
                self.config.storage_type == StorageType.FILE
                and self.config.provision_type == ProvisionType.PERSISTENT
            ):
                tags["UseCase3"] = use_cases["UseCase3"]
            elif (
                self.config.storage_type == StorageType.FILE
                and self.config.provision_type == ProvisionType.DYNAMIC
            ):
                tags["UseCase4"] = use_cases["UseCase4"]

            # Add a tag for whether shared key access is disabled
            if not self.config.allow_shared_key_access:
                tags["KeyAccess"] = "disabled"

            # Use Azure CLI for creating resource group (so we can show the command)
            cmd = [
                "az",
                "group",
                "create",
                "-n",
                self.config.resource_group,
                "-l",
                self.config.location,
            ]

            # Add tags as separate arguments
            if tags:
                cmd.append("--tags")
                for key, value in tags.items():
                    cmd.append(f"{key}={value}")

            result = run_command(
                cmd,
                description=f"Create resource group {self.config.resource_group}",
                display=True,
            )

            if result.returncode == 0:
                # Extract the actual ID from the JSON result if needed
                self.resource_group_id = result.stdout.strip()

            display_command_result(
                result,
                success_message=f"Resource group created: {self.config.resource_group}",
                error_message=f"Failed to create resource group: {self.config.resource_group}",
                show_output=False,
            )

    def create_managed_identity(self) -> None:
        """Create managed identity for workload identity."""
        with console.status(
            f"Creating managed identity: {self.config.identity_name}..."
        ):
            # Create the identity using Azure CLI since the SDK is complex for this
            create_cmd = [
                "az",
                "identity",
                "create",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.identity_name,
                "--location",
                self.config.location,
                "--query",
                "id",
                "-o",
                "tsv",
            ]

            create_result = run_command(
                create_cmd,
                description=f"Create managed identity {self.config.identity_name}",
            )

            display_command_result(
                create_result,
                success_message=f"Created managed identity: {self.config.identity_name}",
                error_message=f"Failed to create managed identity",
                show_output=False,
            )

            # Get the client ID
            client_id_cmd = [
                "az",
                "identity",
                "show",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.identity_name,
                "--query",
                "clientId",
                "-o",
                "tsv",
            ]

            client_id_result = run_command(
                client_id_cmd,
                description="Get managed identity client ID",
                display=False,  # Don't display this command to reduce clutter
            )

            self.identity_client_id = client_id_result.stdout.strip()

            # Get the principal ID
            principal_id_cmd = [
                "az",
                "identity",
                "show",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.identity_name,
                "--query",
                "principalId",
                "-o",
                "tsv",
            ]

            principal_id_result = run_command(
                principal_id_cmd,
                description="Get managed identity principal ID",
                display=False,  # Don't display this command to reduce clutter
            )

            self.identity_principal_id = principal_id_result.stdout.strip()

            # Display identity details in a table
            identity_table = Table(title="Managed Identity Details", box=ROUNDED)
            identity_table.add_column("Attribute", style="cyan")
            identity_table.add_column("Value", style="green")

            identity_table.add_row("Name", self.config.identity_name)
            identity_table.add_row("Client ID", self.identity_client_id)
            identity_table.add_row("Principal ID", self.identity_principal_id)

            console.print(identity_table)

            # Ensure identity IDs were properly retrieved
            if not self.identity_client_id or not self.identity_principal_id:
                raise Exception("Failed to retrieve managed identity IDs properly")

    def create_storage_account(self) -> None:
        """Create storage account and container/file share."""
        # Get the appropriate shared key access setting from the config
        allow_shared_key_access = self.config.allow_shared_key_access

        with console.status(
            f"Creating storage account: {self.config.storage_account}..."
        ):
            # Ensure the storage account doesn't already exist
            # This can happen if previous run failed partway through
            # Check if storage account exists without showing command
            check_exists = subprocess.run(
                [
                    "az",
                    "storage",
                    "account",
                    "show",
                    "--name",
                    self.config.storage_account,
                    "--query",
                    "name",
                    "-o",
                    "tsv",
                ],
                capture_output=True,
                text=True,
                # Don't display this command to reduce clutter
            )

            if check_exists.returncode == 0 and check_exists.stdout.strip():
                console.print(
                    f"Storage account already exists: [bold]{self.config.storage_account}[/bold]. Using existing account."
                )

                # Get the account ID for the existing account (without showing command)
                account_id_result = subprocess.run(
                    [
                        "az",
                        "storage",
                        "account",
                        "show",
                        "--name",
                        self.config.storage_account,
                        "--query",
                        "id",
                        "-o",
                        "tsv",
                    ],
                    capture_output=True,
                    text=True,
                    # Don't display this command to reduce clutter
                )
                self.storage_account_id = account_id_result.stdout.strip()
            else:
                # Create storage account with retries
                max_retries = 3
                retry_count = 0
                success = False

                while retry_count < max_retries and not success:
                    # Build the command for storage account creation
                    storage_cmd = [
                        "az",
                        "storage",
                        "account",
                        "create",
                        "--resource-group",
                        self.config.resource_group,
                        "--name",
                        self.config.storage_account,
                        "--location",
                        self.config.location,
                        "--sku",
                        "Standard_LRS",
                        "--kind",
                        "StorageV2",
                        "--allow-shared-key-access",
                        str(allow_shared_key_access).lower(),
                        "--allow-blob-public-access",
                        "false",
                        "--query",
                        "id",
                        "-o",
                        "tsv",
                    ]

                    # Run the command using our helper that displays the command
                    key_status = "enabled" if allow_shared_key_access else "disabled"
                    storage_result = run_command(
                        storage_cmd,
                        description=f"Create storage account with shared keys {key_status}",
                        display=True,
                    )

                    if storage_result.returncode == 0 and storage_result.stdout.strip():
                        self.storage_account_id = storage_result.stdout.strip()
                        success = True
                    else:
                        retry_count += 1
                        if retry_count < max_retries:
                            console.print(
                                f"[yellow]Retrying storage account creation ({retry_count}/{max_retries})[/yellow]"
                            )
                            time.sleep(5)  # Wait between retries

                if not success:
                    error_msg = storage_result.stderr.strip() or "Unknown error"
                    raise Exception(
                        f"Failed to create storage account after {max_retries} attempts: {error_msg}"
                    )

                # Verify the storage account was created successfully (without showing command)
                verify_result = subprocess.run(
                    [
                        "az",
                        "storage",
                        "account",
                        "show",
                        "--name",
                        self.config.storage_account,
                        "--query",
                        "name",
                        "-o",
                        "tsv",
                    ],
                    capture_output=True,
                    text=True,
                    # Don't display this command to reduce clutter
                )

                if verify_result.returncode != 0 or not verify_result.stdout.strip():
                    raise Exception(
                        f"Storage account {self.config.storage_account} was not created successfully"
                    )

                # Display storage account details in the command output
                key_status_display = (
                    "ENABLED" if allow_shared_key_access else "DISABLED (Keyless)"
                )
                key_style = "" if allow_shared_key_access else "bold yellow"

                console.print(
                    f"✓ Storage account created and verified: [bold]{self.config.storage_account}[/bold]"
                )

                # Create a detailed table for the storage account
                storage_detail_table = Table(
                    title="Storage Account Details", box=ROUNDED
                )
                storage_detail_table.add_column("Attribute", style="cyan")
                storage_detail_table.add_column("Value", style="green")

                storage_detail_table.add_row("Name", self.config.storage_account)
                storage_detail_table.add_row(
                    "Resource Group", self.config.resource_group
                )
                storage_detail_table.add_row("Location", self.config.location)
                storage_detail_table.add_row(
                    "Shared Key Access",
                    (
                        f"[{key_style}]{key_status_display}[/]"
                        if key_style
                        else key_status_display
                    ),
                )
                storage_detail_table.add_row("SKU", "Standard_LRS")
                storage_detail_table.add_row("Kind", "StorageV2")

                console.print(storage_detail_table)

            # Create container or file share based on storage type
            if self.config.storage_type == StorageType.BLOB:
                # Create blob container
                auth_mode = "login" if not allow_shared_key_access else ""
                auth_args = ["--auth-mode", auth_mode] if auth_mode else []

                container_cmd = [
                    "az",
                    "storage",
                    "container",
                    "create",
                    "--name",
                    self.blob_container_name,
                    "--account-name",
                    self.config.storage_account,
                    *auth_args,
                ]

                container_result = subprocess.run(
                    container_cmd, capture_output=True, text=True
                )
                if container_result.returncode != 0:
                    container_error = container_result.stderr.strip() or "Unknown error"
                    raise Exception(
                        f"Failed to create blob container: {container_error}"
                    )

                console.print(
                    f"✓ Blob container created: [bold]{self.blob_container_name}[/bold]"
                )
            else:
                # Create file share
                share_result = subprocess.run(
                    [
                        "az",
                        "storage",
                        "share",
                        "create",
                        "--name",
                        self.file_share_name,
                        "--account-name",
                        self.config.storage_account,
                    ],
                    capture_output=True,
                    text=True,
                )

                if share_result.returncode != 0:
                    share_error = share_result.stderr.strip() or "Unknown error"
                    raise Exception(f"Failed to create file share: {share_error}")

                console.print(
                    f"✓ File share created: [bold]{self.file_share_name}[/bold]"
                )

    def assign_roles(self) -> None:
        """Assign required roles to the managed identity."""
        with console.status("Assigning required roles..."):
            # For dynamic provisioning, we skip the storage account-specific role assignments
            # since we don't have a storage account ID yet
            if (
                not self.storage_account_id
                and self.config.provision_type == ProvisionType.DYNAMIC
            ):
                console.print(
                    "Skipping storage account role assignments for dynamic provisioning"
                )
            else:
                # First, assign the Storage Account Key Operator Service Role
                # This allows the CSI driver to access the storage account keys (listKeys permission)
                key_operator_result = run_command(
                    [
                        "az",
                        "role",
                        "assignment",
                        "create",
                        "--assignee",
                        self.identity_principal_id,
                        "--role",
                        "Storage Account Key Operator Service Role",
                        "--scope",
                        self.storage_account_id,
                    ],
                    description="Assign Storage Account Key Operator Service Role",
                    display=True,
                )

                display_command_result(
                    key_operator_result,
                    success_message=f"Storage Account Key Operator Service Role assigned to identity",
                    error_message=f"Failed to assign Storage Account Key Operator Service Role",
                    show_output=False,
                )

                # Now assign the appropriate data role based on storage type
                if self.config.storage_type == StorageType.BLOB:
                    role_name = "Storage Blob Data Contributor"
                    role_description = "Blob Data Contributor role"
                else:
                    role_name = "Storage File Data SMB Share Contributor"
                    role_description = "File Data SMB Share Contributor role"

                data_role_result = run_command(
                    [
                        "az",
                        "role",
                        "assignment",
                        "create",
                        "--assignee",
                        self.identity_principal_id,
                        "--role",
                        role_name,
                        "--scope",
                        self.storage_account_id,
                    ],
                    description=f"Assign {role_description}",
                    display=True,
                )

                display_command_result(
                    data_role_result,
                    success_message=f"{role_description} assigned to identity",
                    error_message=f"Failed to assign {role_description}",
                    show_output=False,
                )

            # Get the node resource group
            node_resource_group_cmd = [
                "az",
                "aks",
                "show",
                "--name",
                self.config.cluster_name,
                "--resource-group",
                self.config.resource_group,
                "--query",
                "nodeResourceGroup",
                "-o",
                "tsv",
            ]
            node_rg_result = run_command(
                node_resource_group_cmd,
                description="Get AKS node resource group",
                display=False,
            )
            node_resource_group = node_rg_result.stdout.strip()

            # Get the node resource group ID
            node_rg_id_cmd = [
                "az",
                "group",
                "show",
                "--name",
                node_resource_group,
                "--query",
                "id",
                "-o",
                "tsv",
            ]
            node_rg_id_result = run_command(
                node_rg_id_cmd,
                description="Get node resource group ID",
                display=False,
            )
            node_rg_id = node_rg_id_result.stdout.strip()

            # Assign Reader role on the node resource group
            if node_rg_id:
                reader_role_result = run_command(
                    [
                        "az",
                        "role",
                        "assignment",
                        "create",
                        "--assignee",
                        self.identity_principal_id,
                        "--role",
                        "Reader",
                        "--scope",
                        node_rg_id,
                    ],
                    description="Assign Reader role on node resource group",
                    display=True,
                )

                display_command_result(
                    reader_role_result,
                    success_message=f"✓ Reader role assigned to identity on node resource group: {node_resource_group}",
                    error_message=f"Failed to assign Reader role on node resource group",
                    show_output=False,
                )

            console.print(
                f"✓ Required roles assigned to identity: [bold]{self.config.identity_name}[/bold]"
            )

    def create_aks_cluster(self) -> None:
        """Create AKS cluster with workload identity enabled."""
        with console.status(
            f"Creating AKS cluster (this may take a few minutes): {self.config.cluster_name}..."
        ):
            # Base command parts
            cmd = [
                "az",
                "aks",
                "create",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.cluster_name,
                "--location",
                self.config.location,
                "--node-count",
                "1",
                "--enable-managed-identity",
                "--enable-oidc-issuer",
                "--enable-workload-identity",
            ]

            # Add blob driver flag if using blob storage
            if self.config.storage_type == StorageType.BLOB:
                cmd.append("--enable-blob-driver")

            # Create the cluster
            create_result = run_command(
                cmd,
                description=f"Create AKS cluster {self.config.cluster_name}",
                display=True,
            )

            display_command_result(
                create_result,
                success_message=f"AKS cluster created: {self.config.cluster_name}",
                error_message=f"Failed to create AKS cluster: {self.config.cluster_name}",
                show_output=False,
            )

            # Get the cluster credentials
            cred_cmd = [
                "az",
                "aks",
                "get-credentials",
                "--resource-group",
                self.config.resource_group,
                "--name",
                self.config.cluster_name,
                "--overwrite-existing",
            ]

            cred_result = run_command(
                cred_cmd, description="Get AKS credentials", display=True
            )

            display_command_result(
                cred_result,
                success_message="AKS credentials obtained successfully",
                error_message="Failed to get AKS credentials",
                show_output=False,
            )

            # Get the OIDC issuer URL
            oidc_cmd = [
                "az",
                "aks",
                "show",
                "--name",
                self.config.cluster_name,
                "--resource-group",
                self.config.resource_group,
                "--query",
                "oidcIssuerProfile.issuerUrl",
                "--output",
                "tsv",
            ]

            oidc_result = run_command(
                oidc_cmd, description="Get OIDC issuer URL", display=True
            )

            self.oidc_issuer_url = oidc_result.stdout.strip()

            # Display AKS information in a table
            aks_table = Table(title="AKS Cluster Details", box=ROUNDED)
            aks_table.add_column("Attribute", style="cyan")
            aks_table.add_column("Value", style="green")

            aks_table.add_row("Cluster Name", self.config.cluster_name)
            aks_table.add_row("Resource Group", self.config.resource_group)
            aks_table.add_row("OIDC Issuer URL", self.oidc_issuer_url)

            console.print(aks_table)

            # Display Storage Account information in a table
            storage_table = Table(title="Storage Account Details", box=ROUNDED)
            storage_table.add_column("Attribute", style="cyan")
            storage_table.add_column("Value", style="green")

            storage_table.add_row("Storage Account", self.config.storage_account)
            storage_table.add_row("Resource Group", self.config.resource_group)
            key_status = (
                "Enabled"
                if self.config.allow_shared_key_access
                else "Disabled (Keyless)"
            )
            key_style = "" if self.config.allow_shared_key_access else "bold yellow"
            storage_table.add_row(
                "Shared Key Access",
                f"[{key_style}]{key_status}[/]" if key_style else key_status,
            )

            console.print(storage_table)

    def configure_workload_identity(self) -> None:
        """Configure workload identity for AKS."""
        with console.status("Configuring workload identity..."):
            # Create service account with workload identity annotation
            sa_yaml = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: storage-sa
  annotations:
    azure.workload.identity/client-id: {self.identity_client_id}
"""

            # Display the service account YAML
            display_k8s_yaml(
                sa_yaml, "Service Account for Workload Identity", "ServiceAccount"
            )

            # Apply the service account YAML
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
                temp_file.write(sa_yaml)
                temp_file.flush()

                # Show a friendlier message
                console.print(
                    "[kubectl]kubectl apply[/kubectl] - Applying ServiceAccount for workload identity"
                )

                # Run kubectl apply without showing the actual command
                result = subprocess.run(
                    ["kubectl", "apply", "-f", temp_file.name],
                    capture_output=True,
                    text=True,
                )

                # Show the result
                if result.returncode == 0:
                    console.print(
                        "[success]✓ ServiceAccount created successfully[/success]"
                    )
                else:
                    console.print("[error]✗ Failed to create ServiceAccount[/error]")
                    if result.stderr and result.stderr.strip():
                        console.print(
                            Panel(
                                result.stderr.strip(),
                                title="Error Output",
                                border_style="error",
                            )
                        )
                    # Exit immediately when kubectl apply fails
                    console.print(
                        "[error]Execution terminated due to kubectl error[/error]"
                    )
                    raise typer.Exit(code=1)

            # Create federated identity credential
            subprocess.run(
                [
                    "az",
                    "identity",
                    "federated-credential",
                    "create",
                    "--name",
                    "storage-credential",
                    "--identity-name",
                    self.config.identity_name,
                    "--resource-group",
                    self.config.resource_group,
                    "--issuer",
                    self.oidc_issuer_url,
                    "--subject",
                    "system:serviceaccount:default:storage-sa",
                    "--audience",
                    "api://AzureADTokenExchange",
                ],
                capture_output=True,
                text=True,
            )

            console.print("✓ Workload identity configured successfully")

    def configure_static_storage(self) -> None:
        """Configure static (persistent) volume provisioning."""
        with console.status("Configuring static storage volume..."):
            if self.config.storage_type == StorageType.BLOB:
                # Create static PV for Blob
                pv_yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: blob-persistent-pv
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
    volumeHandle: {self.config.resource_group}#{self.config.storage_account}#{self.blob_container_name}
    volumeAttributes:
      storageaccount: {self.config.storage_account}
      containerName: {self.blob_container_name}
      clientID: {self.identity_client_id}
      resourcegroup: {self.config.resource_group}
      protocol: fuse
"""
                # PVC that binds to the static PV
                pvc_yaml = f"""
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: blob-persistent-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
  volumeName: blob-persistent-pv
  storageClassName: azureblob-fuse-premium
"""
                # Job to test the storage
                job_yaml = f"""
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
          echo "Hello from static provisioning on Blob Storage" > /mnt/static/test.txt
          ls -l /mnt/static/test.txt
          cat /mnt/static/test.txt
        volumeMounts:
        - name: static
          mountPath: /mnt/static
      volumes:
      - name: static
        persistentVolumeClaim:
          claimName: blob-persistent-pvc
      restartPolicy: Never
"""
            else:  # File storage
                # Create static PV for File
                pv_yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: file-persistent-pv
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
    volumeHandle: {self.config.resource_group}#{self.config.storage_account}#{self.file_share_name}
    volumeAttributes:
      storageaccount: {self.config.storage_account}
      shareName: {self.file_share_name}
      clientID: {self.identity_client_id}
      resourcegroup: {self.config.resource_group}
      protocol: smb
"""
                # PVC that binds to the static PV
                pvc_yaml = f"""
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: file-persistent-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 10Gi
  volumeName: file-persistent-pv
  storageClassName: azurefile-csi
"""
                # Job to test the storage
                job_yaml = f"""
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
          echo "Hello from static provisioning on Azure Files" > /mnt/static/test.txt
          ls -l /mnt/static/test.txt
          cat /mnt/static/test.txt
        volumeMounts:
        - name: static
          mountPath: /mnt/static
      volumes:
      - name: static
        persistentVolumeClaim:
          claimName: file-persistent-pvc
      restartPolicy: Never
"""

            # Display and apply the Kubernetes YAML files
            for yaml_content, description in [
                (pv_yaml, "Persistent Volume"),
                (pvc_yaml, "Persistent Volume Claim"),
                (job_yaml, "Test Job"),
            ]:
                # Display the YAML for educational purposes
                display_k8s_yaml(
                    yaml_content,
                    f"{description} ({self.config.storage_type.value} Storage)",
                )

                # Apply the YAML
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
                    temp_file.write(yaml_content)
                    temp_file.flush()

                    # Show a friendlier command to the user (hiding the temp file path)
                    console.print(
                        f"[kubectl]kubectl apply[/kubectl] - Applying {description} for {self.config.storage_type.value} Storage"
                    )

                    # Run kubectl apply without showing the actual command
                    result = subprocess.run(
                        ["kubectl", "apply", "-f", temp_file.name],
                        capture_output=True,
                        text=True,
                    )

                    # Show the result
                    if result.returncode == 0:
                        console.print(
                            f"[success]✓ {description} created successfully[/success]"
                        )
                    else:
                        console.print(
                            f"[error]✗ Failed to create {description}[/error]"
                        )
                        if result.stderr and result.stderr.strip():
                            console.print(
                                Panel(
                                    result.stderr.strip(),
                                    title="Error Output",
                                    border_style="error",
                                )
                            )
                        # Exit immediately when kubectl apply fails
                        console.print(
                            "[error]Execution terminated due to kubectl error[/error]"
                        )
                        raise typer.Exit(code=1)

                    os.unlink(temp_file.name)

            # Wait for the job to complete
            storage_type_name = (
                "blob" if self.config.storage_type == StorageType.BLOB else "file"
            )
            job_name = f"static-{storage_type_name}-creator"

            # Use kubectl wait command to wait for job completion
            wait_cmd = [
                "kubectl",
                "wait",
                "--for=condition=complete",
                f"job/{job_name}",
                "--timeout=60s",
            ]

            wait_result = subprocess.run(wait_cmd, capture_output=True, text=True)

            if wait_result.returncode != 0:
                console.print(
                    "[bold red]Job did not complete within the timeout period[/bold red]"
                )
                return

            # Show the job logs
            logs_result = subprocess.run(
                ["kubectl", "logs", f"job/{job_name}"], capture_output=True, text=True
            )

            console.print(
                f"✓ Static {self.config.storage_type.value} storage configured successfully"
            )
            console.print(
                Panel(
                    logs_result.stdout,
                    title=f"[bold]Static {self.config.storage_type.value} Storage Test Results[/bold]",
                )
            )

    def configure_dynamic_storage(self) -> None:
        """Configure dynamic volume provisioning."""
        with console.status("Configuring dynamic storage volume..."):
            if self.config.storage_type == StorageType.BLOB:
                # For Blob storage, use the azureblob-fuse-premium storage class
                pvc_yaml = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: blob-dynamic-pvc
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azureblob-fuse-premium
  resources:
    requests:
      storage: 5Gi
"""
                # Job to test the storage
                job_yaml = """
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
          echo "Hello from dynamic provisioning on Blob Storage" > /mnt/dynamic/test.txt
          ls -l /mnt/dynamic/test.txt
          cat /mnt/dynamic/test.txt
        volumeMounts:
        - name: dynamic
          mountPath: /mnt/dynamic
      volumes:
      - name: dynamic
        persistentVolumeClaim:
          claimName: blob-dynamic-pvc
      restartPolicy: Never
"""
            else:  # File storage
                # For File storage, use the azurefile-csi storage class
                pvc_yaml = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: file-dynamic-pvc
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: azurefile-csi
  resources:
    requests:
      storage: 5Gi
"""
                # Job to test the storage
                job_yaml = """
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
          echo "Hello from dynamic provisioning on Azure Files" > /mnt/dynamic/test.txt
          ls -l /mnt/dynamic/test.txt
          cat /mnt/dynamic/test.txt
        volumeMounts:
        - name: dynamic
          mountPath: /mnt/dynamic
      volumes:
      - name: dynamic
        persistentVolumeClaim:
          claimName: file-dynamic-pvc
      restartPolicy: Never
"""

            # Apply the created YAML files
            for yaml_content, description in [
                (pvc_yaml, "Persistent Volume Claim"),
                (job_yaml, "Test Job"),
            ]:
                # Display the YAML for educational purposes
                display_k8s_yaml(
                    yaml_content,
                    f"{description} ({self.config.storage_type.value} Storage - Dynamic)",
                )

                # Apply the YAML
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
                    temp_file.write(yaml_content)
                    temp_file.flush()

                    # Show a friendlier command to the user
                    console.print(
                        f"[kubectl]kubectl apply[/kubectl] - Applying {description} for {self.config.storage_type.value} Storage (Dynamic)"
                    )

                    # Run kubectl apply without showing the actual command
                    result = subprocess.run(
                        ["kubectl", "apply", "-f", temp_file.name],
                        capture_output=True,
                        text=True,
                    )

                    # Show the result
                    if result.returncode == 0:
                        console.print(
                            f"[success]✓ {description} created successfully[/success]"
                        )
                    else:
                        console.print(
                            f"[error]✗ Failed to create {description}[/error]"
                        )
                        if result.stderr and result.stderr.strip():
                            console.print(
                                Panel(
                                    result.stderr.strip(),
                                    title="Error Output",
                                    border_style="error",
                                )
                            )
                        # Exit immediately when kubectl apply fails
                        console.print(
                            "[error]Execution terminated due to kubectl error[/error]"
                        )
                        raise typer.Exit(code=1)

                    os.unlink(temp_file.name)

            # Wait for the job to complete
            storage_type_name = (
                "blob" if self.config.storage_type == StorageType.BLOB else "file"
            )
            job_name = (
                f"dynamic-{storage_type_name}-creator"
                if self.config.storage_type == StorageType.BLOB
                else "dynamic-file-creator"
            )

            # Use kubectl wait command to wait for job completion - dynamic provisioning needs longer timeout
            wait_cmd = [
                "kubectl",
                "wait",
                "--for=condition=complete",
                f"job/{job_name}",
                "--timeout=120s",  # Dynamic provisioning takes longer
            ]

            wait_result = subprocess.run(wait_cmd, capture_output=True, text=True)

            if wait_result.returncode != 0:
                console.print(
                    "[bold red]Job did not complete within the timeout period[/bold red]"
                )
                return

            # Show the job logs
            logs_result = subprocess.run(
                ["kubectl", "logs", f"job/{job_name}"], capture_output=True, text=True
            )

            console.print(
                f"✓ Dynamic {self.config.storage_type.value} storage configured successfully"
            )
            console.print(
                Panel(
                    logs_result.stdout,
                    title=f"[bold]Dynamic {self.config.storage_type.value} Storage Test Results[/bold]",
                )
            )


def show_summary(config: Config, keyless_support: bool) -> None:
    """Show a summary of the configuration and its keyless support."""
    table = Table(title="AKS Storage Integration Summary")

    table.add_column("Configuration", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Storage Type", config.storage_type.value)
    table.add_row("Provision Type", config.provision_type.value)
    table.add_row("Resource Group", config.resource_group)
    table.add_row("Storage Account", config.storage_account)
    table.add_row("AKS Cluster", config.cluster_name)
    table.add_row("Managed Identity", config.identity_name)
    table.add_row(
        "Shared Key Access",
        "✅ Enabled" if config.allow_shared_key_access else "❌ Disabled (Keyless)",
    )
    table.add_row(
        "Keyless Support", "✅ Supported" if keyless_support else "❌ Not Supported"
    )

    console.print(table)


@app.command()
def main(
    group: str = typer.Option("aks-storage-poc", help="Group name for the settings"),
    storage: StorageType = typer.Option(
        None, help="Storage Type to use (Blob or File)"
    ),
    provision: ProvisionType = typer.Option(
        None, help="Provision Type to use (Persistent or Dynamic)"
    ),
    disable_shared_key: bool = typer.Option(
        False,
        "--disable-shared-key",
        help="Disable shared key access on the storage account.",
    ),
) -> None:
    """
    Test consuming Azure Storage from Azure Kubernetes Service (AKS).

    Usage modes: Specify --storage and --provision to target specific use cases.
    """

    # Convert disable_shared_key to allow_shared_key_access (inverse logic)
    allow_shared_key_access = not disable_shared_key

    # When disable_shared_key is set, enforce Blob Storage with Static Provisioning
    # since that's the only compatible use case
    if disable_shared_key:
        if storage is not None and storage != StorageType.BLOB:
            console.print(
                "[bold red]Error:[/bold red] When --disable-shared-key is used, only Blob Storage is supported."
            )
            raise typer.Exit(code=1)

        if provision is not None and provision != ProvisionType.PERSISTENT:
            console.print(
                "[bold red]Error:[/bold red] When --disable-shared-key is used, only Static (Persistent) provisioning is supported."
            )
            raise typer.Exit(code=1)

        # Force the correct settings for keyless mode
        storage = StorageType.BLOB
        provision = ProvisionType.PERSISTENT
        # Override all use cases mode when keyless is enabled
        run_all_cases = False
        run_all_storage_types = False
        run_all_provision_types = False

        console.print(
            Panel(
                "Keyless mode (--disable-shared-key) only supports Blob Storage with Static Provisioning.\n"
                "Automatically selecting this configuration.",
                title="[bold yellow]Keyless Mode Restriction[/bold yellow]",
                border_style="yellow",
            )
        )
    else:
        # Normal operation with shared key access enabled
        # Determine if we should run all use cases or specific types
        run_all_cases = storage is None and provision is None
        run_all_storage_types = storage is None and provision is not None
        run_all_provision_types = storage is not None and provision is None

    # If only storage parameter is provided, we'll run both provision types
    # If only provision parameter is provided, we'll run both storage types
    # If both parameters are missing, we'll run all four combinations (all use cases)
    # Otherwise, we run the specific combination provided

    # Set defaults for any missing parameters
    if storage is None and provision is not None:
        storage = (
            StorageType.BLOB
        )  # Default storage type when only provision is specified
    if provision is None and storage is not None:
        provision = (
            ProvisionType.PERSISTENT
        )  # Default provision type when only storage is specified

    # Handle running multiple use cases
    if run_all_cases or run_all_storage_types or run_all_provision_types:
        panel_title = "AKS Storage Integration - All Use Cases"
        panel_content = ""

        if run_all_cases:
            panel_title = "AKS Storage Integration - All Use Cases"
            panel_content = (
                "Running all four use cases:\n"
                "1. Blob Storage with Static Provisioning\n"
                "2. Blob Storage with Dynamic Provisioning\n"
                "3. Azure Files with Static Provisioning\n"
                "4. Azure Files with Dynamic Provisioning"
            )
        elif run_all_storage_types:
            panel_title = f"AKS Storage Integration - All Storage Types with {provision.value} Provisioning"
            panel_content = (
                f"Running all storage types with {provision.value} provisioning:\n"
                f"1. Blob Storage with {provision.value} Provisioning\n"
                f"2. Azure Files with {provision.value} Provisioning"
            )
        elif run_all_provision_types:
            panel_title = f"AKS Storage Integration - All Provision Types for {storage.value} Storage"
            panel_content = (
                f"Running all provision types for {storage.value} Storage:\n"
                f"1. {storage.value} Storage with Static Provisioning\n"
                f"2. {storage.value} Storage with Dynamic Provisioning"
            )

        console.print(
            Panel(
                panel_content,
                title=panel_title,
            )
        )

        # Define all use cases
        all_use_cases = [
            {
                "storage": StorageType.BLOB,
                "provision": ProvisionType.PERSISTENT,
                "name": "Blob-Static",
            },
            {
                "storage": StorageType.BLOB,
                "provision": ProvisionType.DYNAMIC,
                "name": "Blob-Dynamic",
            },
            {
                "storage": StorageType.FILE,
                "provision": ProvisionType.PERSISTENT,
                "name": "File-Static",
            },
            {
                "storage": StorageType.FILE,
                "provision": ProvisionType.DYNAMIC,
                "name": "File-Dynamic",
            },
        ]

        # Filter use cases based on what's being run
        use_cases = []

        if run_all_cases:
            use_cases = all_use_cases
        elif run_all_storage_types:
            # Filter for just the provided provision type
            use_cases = [
                case for case in all_use_cases if case["provision"] == provision
            ]
        elif run_all_provision_types:
            # Filter for just the provided storage type
            use_cases = [case for case in all_use_cases if case["storage"] == storage]

        # Set up common infrastructure once
        # For the resource group in all-cases mode, we want to tag with all use cases
        # We'll set it to BLOB+PERSISTENT initially, but update the resource group creation later
        base_config = Config(
            group=group,
            storage_type=StorageType.BLOB,
            provision_type=ProvisionType.PERSISTENT,
            allow_shared_key_access=allow_shared_key_access,  # Apply the shared key override if specified
        )

        # If disable_shared_key is set, we should not proceed with all use cases
        if disable_shared_key and run_all_cases:
            console.print(
                "[bold red]Error:[/bold red] When --disable-shared-key is used, only Blob Storage with Static Provisioning is supported.\n"
                "This is incompatible with running all use cases."
            )
            raise typer.Exit(code=1)

        try:
            # Create base resource group and AKS cluster
            azure_manager = AzureManager(base_config)

            # Prepare the use cases filter based on what we're running
            use_cases_filter = None
            if run_all_cases:
                # For "all use cases", don't filter anything
                azure_manager.create_resource_group(all_use_cases=True)
            elif run_all_storage_types:
                # For "all storage types", filter by provision type
                use_cases_filter = {"provision": provision}
                azure_manager.create_resource_group(use_cases_filter=use_cases_filter)
            elif run_all_provision_types:
                # For "all provision types", filter by storage type
                use_cases_filter = {"storage": storage}
                azure_manager.create_resource_group(use_cases_filter=use_cases_filter)
            else:
                # For a single use case, don't pass any filters
                azure_manager.create_resource_group()
            azure_manager.create_managed_identity()
            azure_manager.create_aks_cluster()
            azure_manager.configure_workload_identity()

            # Only create a storage account if at least one use case involves static provisioning
            needs_storage_account = False

            # Check if any of the selected use cases need a storage account (static provisioning)
            for case in use_cases:
                if case["provision"] == ProvisionType.PERSISTENT:
                    needs_storage_account = True
                    break

            if needs_storage_account:
                # Create a single storage account for static provisioning cases
                azure_manager.create_storage_account()

                # Verify the storage account was created
                verify_result = subprocess.run(
                    [
                        "az",
                        "storage",
                        "account",
                        "show",
                        "--name",
                        azure_manager.config.storage_account,
                        "--query",
                        "name",
                        "-o",
                        "tsv",
                    ],
                    capture_output=True,
                    text=True,
                )

                if not verify_result.stdout.strip():
                    raise Exception(
                        f"Storage account {azure_manager.config.storage_account} was not created successfully"
                    )

                # Get storage account ID for later use
                account_id_result = subprocess.run(
                    [
                        "az",
                        "storage",
                        "account",
                        "show",
                        "--name",
                        azure_manager.config.storage_account,
                        "--query",
                        "id",
                        "-o",
                        "tsv",
                    ],
                    capture_output=True,
                    text=True,
                )
                azure_manager.storage_account_id = account_id_result.stdout.strip()

                console.print(
                    f"✓ Storage account created for static provisioning cases: [bold]{azure_manager.config.storage_account}[/bold]"
                )
            else:
                console.print(
                    "Skipping storage account creation since all selected use cases use dynamic provisioning"
                )

            # Track results for each use case
            results = []

            # Run each use case
            for i, case in enumerate(use_cases, 1):
                case_storage = case["storage"]
                case_provision = case["provision"]
                case_name = case["name"]

                # Determine keyless support based on storage and provision type
                # Only Blob storage with persistent provisioning supports keyless access when allow_shared_key_access is False
                if (
                    case_storage == StorageType.BLOB
                    and case_provision == ProvisionType.PERSISTENT
                    and not allow_shared_key_access
                ):
                    keyless_support = True
                else:
                    keyless_support = False

                # For dynamic provisioning, we don't need to create a storage account
                # For static provisioning, we'll use a single storage account for both Blob and File

                # Use the base storage account for static provisioning cases
                if case_provision == ProvisionType.PERSISTENT:
                    # For all persistent/static cases, use a single storage account
                    storage_account = base_config.storage_account
                else:
                    # For dynamic cases, we don't need a storage account name as it will be created by K8s
                    storage_account = ""

                # Determine appropriate shared key access for this specific use case
                # Use the specified allow_shared_key_access value
                case_shared_key = allow_shared_key_access

                # Create config for this use case
                case_config = Config(
                    group=group,  # Use the same group as the base config
                    storage_type=case_storage,
                    provision_type=case_provision,
                    unique_id=base_config.unique_id,  # Use the same unique ID as base config
                    resource_group=base_config.resource_group,  # Use the same resource group
                    storage_account=storage_account,  # Use the appropriate storage account logic
                    allow_shared_key_access=case_shared_key,  # Use appropriate shared key setting for this case
                )

                console.print(
                    f"\n[bold]Use Case {i}: {case_storage.value} Storage with {case_provision.value} Provisioning ({case_name})[/bold]"
                )

                try:
                    # Set up case manager
                    case_manager = AzureManager(case_config)
                    # Set identity IDs from base manager
                    case_manager.identity_client_id = azure_manager.identity_client_id
                    case_manager.identity_principal_id = (
                        azure_manager.identity_principal_id
                    )
                    case_manager.oidc_issuer_url = azure_manager.oidc_issuer_url

                    # For static provisioning (both blob and file), use the existing storage account
                    if case_provision == ProvisionType.PERSISTENT:
                        # Use the storage account ID from the base manager
                        case_manager.storage_account_id = (
                            azure_manager.storage_account_id
                        )

                        if case_storage == StorageType.BLOB:
                            # Create blob container in the shared storage account
                            container_cmd = [
                                "az",
                                "storage",
                                "container",
                                "create",
                                "--name",
                                case_manager.blob_container_name,
                                "--account-name",
                                case_manager.config.storage_account,
                            ]

                            container_result = subprocess.run(
                                container_cmd, capture_output=True, text=True
                            )

                            if container_result.returncode == 0:
                                console.print(
                                    f"✓ Blob container created: [bold]{case_manager.blob_container_name}[/bold]"
                                )
                        else:  # File storage
                            # Create file share in the shared storage account
                            share_result = subprocess.run(
                                [
                                    "az",
                                    "storage",
                                    "share",
                                    "create",
                                    "--name",
                                    case_manager.file_share_name,
                                    "--account-name",
                                    case_manager.config.storage_account,
                                ],
                                capture_output=True,
                                text=True,
                            )

                            if share_result.returncode == 0:
                                console.print(
                                    f"✓ File share created: [bold]{case_manager.file_share_name}[/bold]"
                                )

                        console.print(
                            f"✓ Using shared storage account for {case_name}: [bold]{case_manager.config.storage_account}[/bold]"
                        )
                    else:
                        # For dynamic provisioning, we don't need to create or use a storage account
                        console.print(
                            f"✓ Using dynamic provisioning for {case_name} (no storage account needed)"
                        )

                    # Check that we have identity IDs before assigning roles
                    if (
                        not case_manager.identity_client_id
                        or not case_manager.identity_principal_id
                    ):
                        # Copy values from the base manager
                        case_manager.identity_client_id = (
                            azure_manager.identity_client_id
                        )
                        case_manager.identity_principal_id = (
                            azure_manager.identity_principal_id
                        )
                        case_manager.oidc_issuer_url = azure_manager.oidc_issuer_url
                        console.print(
                            f"Using identity IDs from base configuration for case {case_name}"
                        )

                    # Continue with role assignments after verification
                    case_manager.assign_roles()

                    # Configure storage according to provision type
                    if case_provision == ProvisionType.PERSISTENT:
                        case_manager.configure_static_storage()
                    else:
                        case_manager.configure_dynamic_storage()

                    # Record success
                    results.append(
                        {"case": case_name, "success": True, "keyless": keyless_support}
                    )

                except Exception as e:
                    console.print(
                        f"[bold red]Error in {case_name}:[/bold red] {str(e)}"
                    )
                    results.append(
                        {
                            "case": case_name,
                            "success": False,
                            "keyless": keyless_support,
                        }
                    )

            # Show final summary
            console.print("\n[bold]All Use Cases Summary[/bold]")
            summary_table = Table(title="AKS Storage Integration Results")
            summary_table.add_column("Use Case", style="cyan")
            summary_table.add_column("Success", style="green")
            summary_table.add_column("Keyless Support", style="yellow")

            for result in results:
                success_icon = "✅" if result["success"] else "❌"
                keyless_icon = "✅" if result["keyless"] else "❌"
                summary_table.add_row(result["case"], success_icon, keyless_icon)

            console.print(summary_table)

        except Exception as e:
            # Skip using Rich console entirely for error messages
            print("\n\033[91mERROR SETTING UP INFRASTRUCTURE\033[0m")
            print(f"Error details: {str(e)}")
            raise typer.Exit(code=1)

    # Handle single use case mode
    else:
        # Ensure we have values for storage and provision
        if storage is None:
            storage = StorageType.BLOB
        if provision is None:
            provision = ProvisionType.PERSISTENT

        # Display the specific use case that will run
        use_case = ""
        if storage == StorageType.BLOB and provision == ProvisionType.PERSISTENT:
            use_case = "Use Case 1: Blob Storage with Static Provisioning"
        elif storage == StorageType.BLOB and provision == ProvisionType.DYNAMIC:
            use_case = "Use Case 2: Blob Storage with Dynamic Provisioning"
        elif storage == StorageType.FILE and provision == ProvisionType.PERSISTENT:
            use_case = "Use Case 3: File Storage with Static Provisioning"
        elif storage == StorageType.FILE and provision == ProvisionType.DYNAMIC:
            use_case = "Use Case 4: File Storage with Dynamic Provisioning"

        console.print(
            Panel(
                f"Running {use_case}",
                title="AKS Storage Integration - Selected Use Case",
            )
        )

        # Only Blob storage with persistent provisioning supports keyless access when allow_shared_key_access is False
        if (
            storage == StorageType.BLOB
            and provision == ProvisionType.PERSISTENT
            and not allow_shared_key_access
        ):
            keyless_support = True
        else:
            keyless_support = False

        # Only show warnings when attempting to use keyless access (allow_shared_key_access=False) with unsupported combinations
        if not allow_shared_key_access:
            if storage == StorageType.FILE:
                console.print(
                    Panel(
                        "[bold yellow]Warning:[/bold yellow] Azure Files does not support keyless access. "
                        "This use case will be marked as N/A for keyless support.",
                        title="Keyless Support Warning",
                    )
                )
            elif storage == StorageType.BLOB and provision == ProvisionType.DYNAMIC:
                console.print(
                    Panel(
                        "[bold yellow]Warning:[/bold yellow] Dynamic provisioning for Azure Blob storage does not support keyless access. "
                        "This use case will be marked as N/A for keyless support.",
                        title="Keyless Support Warning",
                    )
                )

        # Create the configuration with shared key access setting
        config = Config(
            group=group,
            storage_type=storage,
            provision_type=provision,
            allow_shared_key_access=allow_shared_key_access,
        )

        # Show initial configuration
        console.print(
            Panel(
                f"Starting AKS Storage Integration Setup\n\n"
                f"Group: [bold]{group}[/bold]\n"
                f"Storage Type: [bold]{storage.value}[/bold]\n"
                f"Provision Type: [bold]{provision.value}[/bold]",
                title="AKS Storage Integration",
            )
        )

        try:
            # Create manager and provision resources
            azure_manager = AzureManager(config)

            # Step 1: Provision resource group
            azure_manager.create_resource_group()

            # Step 2: Create managed identity
            azure_manager.create_managed_identity()

            # Step 3: Create storage account
            azure_manager.create_storage_account()

            # Step 4: Assign roles
            azure_manager.assign_roles()

            # Step 5: Create AKS cluster
            azure_manager.create_aks_cluster()

            # Step 6: Configure workload identity
            azure_manager.configure_workload_identity()

            # Step 7: Configure storage based on provision type
            if config.provision_type == ProvisionType.PERSISTENT:
                azure_manager.configure_static_storage()
            else:
                azure_manager.configure_dynamic_storage()

            # Show summary
            show_summary(config, keyless_support)

            console.print(
                Panel(
                    "✅ AKS Storage integration has been successfully configured and tested!",
                    title="Success",
                    style="green",
                )
            )

            # Command history feature removed

        except Exception as e:
            # Skip using Rich console entirely for error messages
            print("\n\033[91mERROR\033[0m")
            print(f"Error details: {str(e)}")
            raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
