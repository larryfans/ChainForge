# Responsible release and restricted access

Contact: **[fanjiluan@gmail.com](mailto:fanjiluan@gmail.com)**  
Last updated: November 6, 2026

ChainForge separates public scientific evidence from operational components that could facilitate misuse. All experiments described in the paper use synthetic data and simulated endpoints; no real service was attacked.

## Artifact tiers

| Public tier | Restricted tier |
| :--- | :--- |
| Sanitized benchmark specifications and task inventories | Executable malicious tools and generated payloads |
| Evaluation metrics, protocols, and configuration documentation | Operational scanner-evasion and attack-restoration prompts |
| Reported aggregate results and analysis code | End-to-end attack-generation and refinement pipelines |
| Sanitized defensive-evaluation materials and defense reproductions, as available | Operational code-generation instructions for malicious tools |

An artifact being in the public tier does not mean its implementation has already been released. The [README](README.md#public-artifacts) distinguishes available materials from work in preparation. Operational components have been removed from the current public tree as part of this maintenance update.

## Current availability

**Access inquiries are accepted; restricted artifact distribution is not open.** A request does not automatically authorize access. Distribution remains subject to the review process below and coordinated-disclosure readiness.

Older Git history may still expose previously published materials. Removing files from the current tree does not remove historical copies, forks, or prior downloads. Historical cleanup and disclosure tracking remain open maintenance items; this update does not claim that every historical copy is access-controlled.

## How to request access

Email `fanjiluan@gmail.com` with:

1. Your name, institution, institutional email address, and a public institutional profile or equivalent affiliation evidence.
2. Your defensive research purpose and the specific artifacts requested.
3. The intended evaluation environment and how access will be limited to authorized researchers.

Do not post affiliation documents, personal information, or sensitive technical material in public GitHub Issues. Initial inquiries do not require identity documents or confidential institutional records.

## Review and distribution process

Before any restricted distribution, maintainers must:

1. Verify institutional affiliation and assess the proposed defensive use.
2. Confirm that the requested materials are necessary and that relevant disclosure obligations have been considered.
3. Obtain agreement to defensive-research terms: authorized synthetic or sandboxed evaluation, no unauthorized testing against real services, and no redistribution of restricted artifacts.
4. Approve only the required assets and versions. Access will be staged, prioritizing platform defenders and scanner vendors.
5. Use a private, access-controlled distribution channel and retain an internal record of the request, review decision, terms acceptance, asset versions, grant date, and revocation where applicable.

Access records and affiliation evidence are maintained privately. Public status updates must not identify applicants or expose their research details without permission. Access may be refused or withdrawn when the agreed conditions are not met.

## Coordinated disclosure

The paper commits to disclosure to Glama, Smithery, MCP.so, VirusTotal, AntGroup MCPScan, Tencent A.I.G, and Cisco MCP Scanner before public artifact release. The [security policy](SECURITY.md#coordinated-disclosure-status) records the public status of that commitment. This maintenance update does not assert that disclosure has been completed and is not an announcement of a complete artifact release.

## Licenses and third-party materials

The repository's [MIT license](LICENSE) applies to material distributed under that license. Restricted distribution is a separate process; this policy does not retroactively change licenses or recall copies already distributed. Third-party code remains subject to its original license and attribution requirements. The local `agentdojo/` tree derives from [AgentDojo](https://github.com/ethz-spylab/agentdojo); retaining a source snapshot does not imply endorsement by its maintainers.
