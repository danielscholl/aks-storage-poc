## Executive Summary: Breaking Free from Storage Keys

Microsoft should prioritize enabling Kubernetes `StorageClass` support to dynamically provision Azure storage accounts with the `--allow-shared-key-access` option disabled. The current limitation is not with workload identity, which works as intended, but rather with the inability to automatically provision storage accounts that comply with security best practices. 

Azure Files CSI drivers lack support for keyless storage accounts, meaning that even manually provisioned, compliant storage accounts remain unusable with Azure Files. Organizations are limited to using `blobfuse` ([azure-storage-fuse](https://github.com/Azure/azure-storage-fuse)) for secure file mounts, as NFS lacks network encryption. These constraints hinder full utilization of AKS scalability, automation, and compliance capabilities.

> 🔍 **Dig Deeper:** For additional context, see the [defined problem statement](../.github/prompts/research.prompt.md) and the AI-generated [research document](./research.md). These resources provide detailed background on the motivation, methodology, and validation behind this proof of concept.


### Key Challenges

The primary technical gap lies in Kubernetes' inability to dynamically provision Azure storage accounts via `StorageClass` with the `--allow-shared-key-access` setting disabled. Without this capability, organizations cannot automatically enforce essential security best practices. 

Manually provisioned storage accounts without key access enabled remain incompatible with Azure Files due to the Azure Files CSI driver's dependency on storage account keys. As a result, organizations must choose between automated provisioning and strict security compliance, achieving both simultaneously is currently impossible.

| Capability | Azure Blob | Azure Files |
|------------|------------|-------------|
| Dynamic Provisioning | ❌ Not supported | ❌ Not supported |
| Keyless Access | ✅ Supported (static only) | ❌ Not supported |
| Security Compliance | ✅ Meets best practices | ❌ Requires key access |


### The Automation Gap

Dynamic provisioning is a core Kubernetes capability that enables the automatic creation and lifecycle management of persistent storage. In an AKS environment, this greatly reduces manual effort, accelerates application deployment, and supports scale with confidence.

The lack of keyless support in Azure Files breaks this model entirely. Without the ability to dynamically provision secure storage, organizations face growing friction between compliance goals and automation strategies.

#### What's Impacted

- **Blocked Automation.** Dynamic provisioning workflows with Azure Files are off-limits, forcing teams to fall back on manual or static approaches.
- **Compliance Risks.** Security-sensitive environments (e.g., PCI DSS, HIPAA) are stuck using key-based authentication, a direct violation of best practices.
- **Operational Overhead.** Even with scripting via ARM or Bicep, provisioning happens outside the Kubernetes lifecycle, introducing lag, complexity, and risk.
- **Limited Secure Options.** Blobfuse is often the only viable alternative, but it’s not always a fit.

#### Why Blob Isn't Always the Answer

- **Performance Constraints.** Blob storage underperforms Azure Files in high-IOPS or low-latency scenarios.
- **Use Case Mismatch.** It’s purpose-built for object storage, not general-purpose file shares, leaving a gap in capability.


### Microsoft's Current Acknowledgment

Microsoft publicly acknowledges the limitations affecting Azure Files with storage accounts that have key access disabled. Although active community discussions and Microsoft’s acknowledgment exist, no clear roadmap or timeline has been provided. This unresolved gap continues to be a significant compliance barrier for organizations.

### Proposed Workaround and Its Limitations

Currently, organizations must manually provision storage accounts using ARM or Bicep templates, assign RBAC permissions, and define static Persistent Volumes. However, even manually provisioned storage accounts with key access disabled remain incompatible with Azure Files. While this workaround ensures compliance, it introduces substantial operational overhead and undermines Kubernetes' dynamic provisioning advantages.

### Conclusion

Organizations requiring stringent compliance currently face complex manual provisioning processes, limiting Kubernetes scalability and automation potential. Resolving Azure Files' dependency on storage keys is critical. Microsoft must prioritize enabling secure, keyless Azure Files provisioning within Kubernetes workloads to reduce operational complexity and fully realize Kubernetes' automation capabilities.
