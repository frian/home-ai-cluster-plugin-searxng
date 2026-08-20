# Agent Guidelines

Architecture is owned by the upstream Home AI Cluster RFCs. RFC-0078 and
RFC-0079 govern this package; agents implement those accepted decisions and do
not expand them.

Endpoint/configuration/authentication/retry/pagination/result-fetch changes
require upstream architectural review and RFC work. Preserve local-first,
privacy-first, boring-solutions-first behavior. Do not log sensitive queries or
provider responses.
