---
description: Stand up a new customer environment using the new-customer-stamp skill.
---

Invoke the `new-customer-stamp` skill to onboard a new customer with the full strata infrastructure.

Ask the user for:

1. Customer identifier (used as resource prefix).
2. AWS region.
3. Data mart connection info (JDBC URL, subnet, security groups, AZ).
4. The data-mart credentials (separately — these go directly into Secrets Manager, never paste in chat).
5. Whether this is a customer-owned account or a vendor-owned dedicated account.

Walk them through the deployment checklist from the skill.
