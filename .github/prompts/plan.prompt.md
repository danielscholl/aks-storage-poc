## Problem

We want to create a markdown file that documents the steps in an easy way to understand and follow to demonstrate the integration of Azure Storage services with Azure Kubernetes Service (AKS).  Additionally, we want to implement a Single File python uv script that demonstrates the implementation.

We are writing this in Github Copilot, so recording the important files in a `.github/copilot-instructions.md` file is important.


## Supporting Information

### Tools

#### `az`

Use `az` as the cli tool to create and manage azure resources.

#### `kubectl`

Use `kubectl` as the cli tool to create and manage kubernetes resources.


### File Structure

Recommended file structure:

<readonly-files>

#### `.github/prompts/overview.prompt.md`

A file that outlines the requirements for the PoC.

#### `.github/prompts/plan.prompt.md`

A prompt file that outlines step1 of the PoC.

#### `ai-docs/aks-blob_wi_doc.md`

A file that documents how to use Azure Blob Storage with Workload Identity.

#### `ai-docs/aks-file_wi_doc.md`

A file that documents how to use Azure Files with Workload Identity.

</readonly-files>

<editable-files>

#### `README.md`

The markdown file that is the PoC.

Recommended headers:

- # Breaking Free from Keys: AKS & Azure Storage Integration PoC
- ## Important Notes
- ## Prerequisites
- ## Core Components
- ## Step by Step Implementation
- ### Step 1: Create Azure Resources
- ### Step 2: Configure Workload Identity for AKS
- ### Step 3: Persistent Volume Configuration
- #### Azure Blob Storage Configuration
- #### Azure Files Storage Configuration
- ### Step 4: Dynamic Volume Configuration
- #### Azure Blob Storage Configuration
- #### Azure Files Storage Configuration


## Steps To Complete

- READ .github/prompts/poc_overview.md to understand the PoC Objectives.
- READ ai-docs/aks-blob_wi_doc.md to understand how to use Blob Storage.
- READ ai-docs/aks-file_wi_doc.md to understand how to use Azure Files.
- CREATE the `README.md` file with the recommended headers and structure.
- DOCUMENT a manual azure cli and kubectl tutorial in the `README.md` file that consolidates both Blob and File approaches into something that can easily be understood by a reader.
- CREATE the `script.py` uv script that follows the implementation plan below that allows engineers to easily test capabilities of Azure Storage with AKS in their subscriptions.

### Python Script Implementation Plan

As engineers we often have to test resource configuration to find out if it works in our subscription.  Following tutorials can be a pain.

This is a simple tool that will allow you to test consuming Azure Storage from Azure Kubernetes (AKS) in a subscription.


THINK ABOUT THIS SPEC BEFORE IMPLEMENTING IT
--------------------------------------------

## Key Features
- Easy to setup and use running a uv script
- Specify the storage type to test
- Python Rich Console with Panels enhance the user experience

## Project Structure
- All scripts should be placed in the `scripts/` directory
- The project should use a pytest.ini and pyproject.toml for configuration
- Tests for each script are included within the script file itself
- Use `uv run pytest scripts/script-name.py` to run tests for a specific script

### Validation Process
1. Code should pass validation using the python black tool.
2. Code should pass static type checking using the python mypy tool.
3. After initial implementation, run `uv run pytest scripts/script-name.py -v` and show the output
4. If any tests fail, fix the implementation and run tests again until all tests pass
5. Demonstrate validation was successful by:
   - Show the test output with all tests passing
   - Include a brief summary of what was validated
   - Confirm that script functionality matches all requirements in the specification
6. Always validate both functionality and code quality (typing, error handling, etc.)
7. Never consider implementation complete until explicit validation is performed and successful

## Tests
1. The script should be written in a way that is testable.
2. Tests should be included within the same file.

## Implementation Notes
- SCRIPT_NAME = `aks-storage.py`
- CREATE a **single, self-contained Python file** (`scripts/aks-storage.py`) that contains all functionality including tests
- READ ai_docs/aks_blob_wi_doc.md to understand how to work with blob storage in AKS
- READ ai_docs/aks_file_wi_doc.md to understand how to work with file storage in AKS
- The script should use the uv script header format for dependencies
- The script should use typer for command-line argument parsing
- USE the Azure libraries (SDK) for Python
- USE the Kubernetes Python Client

_Example uv script header_
```
#!/usr/bin/env -S uv run --script

# /// script
# dependencies = [
#   "pytest>=7.4.0",
#   "rich>=13.7.0",
#   "azure-identity>=1.15.0",
#   "typer>=0.9.0",
#   "pydantic>=2.5.0",
# ]
# ///
```

## Command Options
Recommended command options.

1. `--group TEXT  Group name for the settings [default: None]` * Required
2. `--storage TEXT Storage Type to use [default: Blob]` * Required
3. `--provision TEXT Provision Type to use [default: Persistent]` * Required
4. `--help`

## Important Details
- Provision a Resource Group
- Provision Managed Identity
- Establish Roles
- Provision AKS
- Provision Storage (If necessary)
- Assign Roles
- Deploy Kubernetes Objects

## CLI Implementation
- Use the Typer library for command-line parsing (NOT argparse)
- Implement as a single command with options, not a multi-command application
- Use `@app.command()` instead of `@app.callback()` for the main function
- All commands are options to the main command, not subcommands

## Script Structure
The script should be a fully self-contained Python file that includes:
1. Main functionality for packing/unpacking/syncing settings
2. CLI argument parsing using Typer
3. Azure integration
4. Python Tests