# Jira OAuth and Rovo MCP setup

1. Create an Atlassian OAuth 2.0 (3LO) application and register this backend callback exactly:
   `https://<api-host>/integrations/jira/callback`.
2. In Atlassian Administration, enable the Rovo MCP server for the organization and ensure the connected users have access to the selected Jira site/project.
3. Configure these application settings (never in frontend variables):

```text
ATLASSIAN_OAUTH_CLIENT_ID=<Atlassian OAuth client id>
ATLASSIAN_OAUTH_CLIENT_SECRET=<Atlassian OAuth client secret>
ATLASSIAN_OAUTH_REDIRECT_URI=https://<api-host>/integrations/jira/callback
JIRA_TOKEN_ENCRYPTION_KEY=<long random server-only secret>
FRONTEND_BASE_URL=https://<frontend-host>
```

Optional settings:

```text
ATLASSIAN_OAUTH_SCOPES=read:jira-work offline_access
ATLASSIAN_ROVO_MCP_URL=https://mcp.atlassian.com/v1/mcp/authv2
ATLASSIAN_MCP_JIRA_SEARCH_TOOL=searchJiraIssuesUsingJql
JIRA_MCP_DEBUG=true
```

`ATLASSIAN_MCP_JIRA_SEARCH_TOOL` defaults to Atlassian's documented `searchJiraIssuesUsingJql`; leave it unset unless your Rovo MCP catalog exposes a different search tool. The integration intentionally exposes no Jira write tool.

Set `JIRA_MCP_DEBUG=true` only while diagnosing an MCP failure. It logs each MCP response status, non-sensitive headers, and body, but intentionally omits authorization and cookie headers. Disable it after collecting the needed logs because Jira response bodies can contain project data.

In the Atlassian OAuth app, add `read:jira-work` under Jira API permissions before authorizing. Rovo MCP's Jira search permission is controlled separately by the Atlassian organization administrator; it is not an OAuth app scope. After deployment, a project lead opens **Configuration > Jira**, authorizes the account, chooses a site, and enters the Jira project key. OAuth tokens are encrypted in Firestore's `jira_connections` collection; project documents retain only non-secret connection metadata.
