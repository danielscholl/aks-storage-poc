# AKS Storage with Workload Identity: Research Request

## Background
This research concerns an Azure AKS solution that requires Storage Account integration with the following security constraints:
- No Storage Account Keys or SAS tokens can be used (organization policy)
- Must use Workload Identity for authentication from AKS to Azure resources
- Storage resources must comply with Azure policies applied to the subscription
- Volumes need to be mounted to Airflow to load DAGs

## Problem Statement
The solution was designed to use StorageClass and PVC to create necessary storage in Azure, but there appear to be compatibility issues between Workload Identity and the default CSI storage drivers.  The issues span a signficant amount of time and confuse things due to the timeline when Workload Identity wasn't supported.

Relevant issues:
- https://github.com/Azure/AKS/issues/3644
- https://github.com/Azure/AKS/issues/3432

NonRelevant Issues:
- Stay away from ideas around AD Domains
- Do not be confused with authentication methods for Azure Storage Files outside the context of AKS and usage of storage from Kubernetes
- Understand that Workload Identity with both Blob and Files is implemented and does work.

## Research Questions
1. What specific technical limitations exist in the current AKS StorageClass or the File CSI drivers that prevent workload identity authentication to storage?
2. Are there any official Microsoft documentation or statements acknowledging this limitation?
3. What is the current roadmap or timeline for addressing this issue based on public information?
4. What security and technical trade-offs are involved with potential workarounds?

## Proposed Workaround Evaluation
Please evaluate this workaround: 
- Manually create storage accounts outside Kubernetes context (via Bicep/ARM)
- Configure these pre-created resources with proper RBAC for workload identity
- Consume these resources via PVC without using StorageClass

Please provide specific configuration examples if this is viable.

## Requested Output Format
- Technical explanation with code examples where applicable
- Citations to official documentation and community sources
- Clear indication of where information is speculative vs. confirmed
- Pros/cons analysis of potential approaches

## Current Code and Error Reference Information

```
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: airflow-storage
provisioner: file.csi.azure.com
parameters:
  skuName: Standard_LRS  
allowVolumeExpansion: true
mountOptions:
  - dir_mode=0777
  - file_mode=0777
  - uid=1000
  - gid=1000
  - mfsymlinks
  - nobrl
reclaimPolicy: Delete
volumeBindingMode: Immediate
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: airflow-logs-pvc
  namespace: airflow
  annotations:
    csi.storage.k8s.io/share-name: "airflow-logs"
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: airflow-storage
  resources:
    requests:
      storage: 5Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: airflow-dags-pvc
  namespace: airflow
  annotations:
    csi.storage.k8s.io/share-name: "airflow-dags"
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: airflow-storage
  resources:
    requests:
      storage: 5Gi
```

```
Name:          airflow-dags-pvc
Namespace:     airflow
StorageClass:  airflow-storage
Status:        Pending
Volume:
Labels:        kustomize.toolkit.fluxcd.io/name=component-airflow
               kustomize.toolkit.fluxcd.io/namespace=flux-system
Annotations:   csi.storage.k8s.io/share-name: airflow-dags
               volume.beta.kubernetes.io/storage-provisioner: file.csi.azure.com
               volume.kubernetes.io/storage-provisioner: file.csi.azure.com
Finalizers:    [kubernetes.io/pvc-protection]
Capacity:
Access Modes:
VolumeMode:    Filesystem
Used By:       airflow-dags-csvdag-upload-w5mtq
               airflow-dags-file-upload-0-p5qwj
Events:
  Type     Reason                Age                    From                                                                                              Message
  ----     ------                ----                   ----                                                                                              -------
  Normal   ExternalProvisioning  108s (x4482 over 18h)  persistentvolume-controller                                                                       Waiting for a volume to be created either by the external provisioner 'file.csi.azure.com' or manually by the system administrator. If volume creation is delayed, please verify that the provisioner is running and correctly registered.
  Normal   Provisioning          99s (x307 over 18h)    file.csi.azure.com_csi-azurefile-controller-6f598dd49-qs489_f6abb1cf-de44-4713-8fec-93ada978119d  External provisioner is provisioning volume for claim "airflow/airflow-dags-pvc"
  Warning  ProvisioningFailed    98s (x298 over 18h)    file.csi.azure.com_csi-azurefile-controller-6f598dd49-qs489_f6abb1cf-de44-4713-8fec-93ada978119d  (combined from similar events): failed to provision volume with StorageClass "airflow-storage": rpc error: code = Internal desc = failed to ensure storage account: failed to create storage account ff88c077ec03944d0afb68f, error: &{false 403 0001-01-01 00:00:00 +0000 UTC {"error":{"code":"RequestDisallowedByPolicy","target":"ff88c077ec03944d0afb68f","message":"Resource 'ff88c077ec03944d0afb68f' was disallowed by policy. Policy identifiers: '[{\"policyAssignment\":{\"name\":\"Storage Account Creation Denied When Shared Access Key Is Enabled\",\"id\":\"/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/policyAssignments/0aedc612157c4e8cbaf9605e\"},\"policyDefinition\":{\"name\":\"Storage Account Creation Denied When Shared Access Key Is Enabled\",\"id\":\"/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/policyDefinitions/73df4292-f7d6-4cfc-af96-99de6835958a\",\"version\":\"1.0.0\"}}]'.","additionalInfo":[{"type":"PolicyViolation","info":{"evaluationDetails":{"evaluatedExpressions":[{"result":"True","expressionKind":"Field","expression":"type","path":"type","expressionValue":"Microsoft.Storage/storageAccounts","targetValue":"Microsoft.Storage/storageAccounts","operator":"Equals"},{"result":"True","expressionKind":"Field","expression":"Microsoft.Storage/storageAccounts/allowSharedKeyAccess","path":"properties.allowSharedKeyAccess","expressionValue":"******","targetValue":"false","operator":"Exists"},{"result":"False","expressionKind":"Field","expression":"name","path":"name","expressionValue":"ff88c077ec03944d0afb68f","targetValue":"system*","operator":"Like"},{"result":"False","expressionKind":"Field","expression":"name","path":"name","expressionValue":"ff88c077ec03944d0afb68f","targetValue":"elastic*","operator":"Like"},{"result":"False","expressionKind":"Field","expression":"name","path":"name","expressionValue":"ff88c077ec03944d0afb68f","targetValue":"*azscripts","operator":"Like"}]},"policyDefinitionId":"/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/policyDefinitions/73df4292-f7d6-4cfc-af96-99de6835958a","policyDefinitionName":"73df4292-f7d6-4cfc-af96-99de6835958a","policyDefinitionDisplayName":"Storage Account Creation Denied When Shared Access Key Is Enabled","policyDefinitionVersion":"1.0.0","policyDefinitionEffect":"deny","policyAssignmentId":"/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/policyAssignments/0aedc612157c4e8cbaf9605e","policyAssignmentName":"0aedc612157c4e8cbaf9605e","policyAssignmentDisplayName":"Storage Account Creation Denied When Shared Access Key Is Enabled","policyAssignmentScope":"/subscriptions/00000000-0000-0000-0000-000000000000","policyAssignmentParameters":{},"policyExemptionIds":[],"policyEnrollmentIds":[]}}]}}}
```


