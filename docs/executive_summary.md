## Executive Summary: Breaking Free from Storage Keys

Microsoft should prioritize enabling Kubernetes `StorageClass` support to dynamically provision Azure storage accounts with the `--allow-shared-key-access` option disabled, while also supporting full keyless access via workload identity in the AKS-managed CSI drivers. The current limitation is twofold: the inability to provision secure, compliant storage accounts dynamically and the lack of full workload identity support in the Microsoft-managed CSI drivers.

Although workload identity can be configured for Azure resources, the AKS-managed Blob and Files CSI drivers require storage account keys, making true keyless access via workload identity unattainable when relying solely on Microsoft-managed solutions. This means that even when organizations take steps to provision secure, compliant storage accounts, they are unable to use them effectively with the managed drivers.

Azure Files, in particular, remains incompatible with keyless storage accounts due to its reliance on access keys. Organizations are limited to using `blobfuse` ([azure-storage-fuse](https://github.com/Azure/azure-storage-fuse)) for secure file mounts, as NFS lacks network encryption. These constraints hinder full utilization of AKS scalability, automation, and compliance capabilities.

> 🔍 **Dig Deeper:** For additional context, see the [defined problem statement](../.github/prompts/research.prompt.md) and the AI-generated [research document](./research.md). These resources provide detailed background on the motivation, methodology, and validation behind this proof of concept.

### Key Challenges

The primary technical gap lies in Kubernetes' inability to dynamically provision Azure storage accounts via `StorageClass` with the `--allow-shared-key-access` setting disabled. This gap is compounded by the fact that Microsoft’s AKS-managed CSI drivers do not support workload identity for keyless access.

Even manually provisioned storage accounts without key access enabled remain incompatible with Azure Files, and the Microsoft-managed drivers prevent workload identity from being used as an alternative. As a result, organizations must choose between automation and compliance—achieving both simultaneously is currently impossible.

| Capability           | Azure Blob                               | Azure Files             |
| -------------------- | ---------------------------------------- | ----------------------- |
| Dynamic Provisioning | ❌ Not supported                          | ❌ Not supported         |
| Keyless Access       | ⚠️  Support for Static Provisioning only | ❌ Not supported         |
| Workload Identity    | ⚠️  Not in managed driver (requires OSS) | ❌ Not in managed driver |
| Security Compliance  | ✅ Meets best practices                   | ❌ Requires key access   |

### The Automation Gap

Dynamic provisioning is a core Kubernetes capability that enables the automatic creation and lifecycle management of persistent storage. In an AKS environment, this greatly reduces manual effort, accelerates application deployment, and supports scale with confidence.

However, due to the lack of keyless and workload identity support in the AKS-managed CSI drivers, this automation model breaks down. Even if you provision secure storage manually, you cannot mount it securely without switching to the open-source drivers.

#### What's Impacted

- **Blocked Automation.** Dynamic provisioning workflows with Azure Files are off-limits, forcing teams to fall back on manual or static approaches.
- **Compliance Risks.** Security-sensitive environments (e.g., PCI DSS, HIPAA) are stuck using key-based authentication, a direct violation of best practices.
- **Operational Overhead.** Even with scripting via ARM or Bicep, provisioning happens outside the Kubernetes lifecycle, introducing lag, complexity, and risk.
- **Limited Secure Options.** Blobfuse is often the only viable alternative, but it’s not always a fit.

For keyless access combined with workload identity, only the open-source (OSS) Blob CSI driver—installed via Helm—supports this configuration. The AKS-managed drivers do not support this scenario.

#### Why Blob Isn't Always the Answer

- **Performance Constraints.** Blob storage underperforms Azure Files in high-IOPS or low-latency scenarios.
- **Use Case Mismatch.** It’s purpose-built for object storage, not general-purpose file shares, leaving a gap in capability.

### Microsoft's Current Acknowledgment

Microsoft publicly acknowledges the limitations affecting Azure Files with storage accounts that have key access disabled. Additionally, the lack of workload identity support in the managed CSI drivers has been widely discussed in community forums. Although Microsoft is aware of these issues, no clear roadmap or timeline has been provided. This unresolved gap continues to be a significant compliance and operational barrier for organizations.

### Proposed Workaround and Its Limitations

Currently, organizations must manually provision storage accounts using ARM or Bicep templates, assign RBAC permissions, and define static Persistent Volumes. Even then, these keyless accounts remain incompatible with Azure Files when using Microsoft’s CSI drivers. While this workaround ensures compliance, it introduces substantial operational overhead and undermines Kubernetes' dynamic provisioning advantages.

Switching to the OSS CSI drivers is the only viable option for achieving both workload identity and keyless access, but doing so shifts operational burden onto teams and bypasses AKS’ managed infrastructure advantages.

### Conclusion

Organizations requiring stringent compliance currently face complex manual provisioning processes, limiting Kubernetes scalability and automation potential. Microsoft must prioritize enabling keyless access and workload identity **within the AKS-managed CSI drivers**. Resolving this twofold dependency is critical to reducing operational complexity and fully realizing Kubernetes' automation capabilities.

