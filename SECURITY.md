# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's
[private vulnerability reporting](https://github.com/ianderso/nara-catalog-mcp/security/advisories/new)
(the **Report a vulnerability** button on the repository's Security tab), not
in a public issue. Include what an attacker controls, what they gain, and the
steps to reproduce it.

You should hear back within a week. Fixes are released for the latest version
only.

## Scope

In scope: this server — how it handles the API key, the files it writes, the
requests it makes, and anything a tool argument or an API response can make it
do.

Out of scope: the National Archives Catalog API, its media host and the Access
to Archival Databases (`aad.archives.gov`), which this project does not
operate. Report problems with those to NARA.

## The security model, briefly

- **The API key** stays in the environment. It is sent only to
  `catalog.archives.gov`, as the `x-api-key` header; it is never sent to the
  media host or to AAD, and never written to the response cache.
- **The AAD client reads one host.** A request hook refuses any host but
  `aad.archives.gov`, and redirects are not followed, so a tool argument
  cannot point it at another site. AAD file and record ids are validated as
  digits before they are sent.
- **The server writes to the Catalog never, and to local disk once.**
  `download_page_image` creates a new file at a path the model chooses, using
  exclusive creation, so it cannot overwrite or truncate an existing file. It
  is annotated as not read-only, so a client can require approval for it.
- **API responses carry untrusted text.** Tags, comments and transcriptions
  are written by anonymous members of the public and are returned to the model
  verbatim, which makes them a channel for prompt injection. The server's
  instructions tell the model to treat that text as material, not as
  instructions, but the model still decides what to do next. An injection that
  leads the model to misuse this server's own tools is in scope; one that
  leads it to misuse other tools the client has connected is a client concern.
- **NAIDs are validated as digits** before being placed in a request path, so
  a tool argument cannot redirect a request to another API route.
