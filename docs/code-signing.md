# Code signing (Windows)

The Windows builds (`pdf45.exe` and the setup) are signed by the CI with
**Azure Artifact Signing**, on behalf of **LAST CHANCE SRL**. The private
key never leaves Microsoft's HSMs and GitHub logs in to Azure with OIDC, so no
password or certificate is stored in the repository.

Signing switches itself on as soon as the repository variable
`AZURE_SIGNING_ACCOUNT` exists. Until then (and for pull requests) the
workflow builds unsigned installers exactly as before.

## One-time setup in Azure

1. **Subscription**: a paid *Pay-As-You-Go* subscription (free/trial
   subscriptions are not accepted by Artifact Signing).
2. **Resource provider**: Subscriptions → *(subscription)* → Resource providers
   → `Microsoft.CodeSigning` → Register.
3. **Artifact Signing account**: create one in *North Europe* (West Europe
   stopped accepting new customers in 2026; Poland Central also works),
   pricing tier *Basic*.
4. **Identity validation** (Azure portal only): give yourself the role
   *Artifact Signing Identity Verifier* on the account, then Identity
   validations → Organization → New identity → Public. Use the company's legal
   name, CUI, registered address, its website and two e-mail addresses on that
   website's domain. The company representative then verifies their ID from a
   phone. Microsoft may ask for documents issued in the last 12 months (ONRC
   registration certificate, proof of domain ownership). Takes 1–20 business days.
5. **Certificate profile**: Certificate profiles → Create → *Public Trust*,
   pick the completed identity validation.

## Let GitHub Actions sign

1. **App registration** (Microsoft Entra ID → App registrations → New), e.g.
   `pdf45-github-signing`. Note its *Application (client) ID* and the
   *Directory (tenant) ID*.
2. **Federated credential** on that app (Certificates & secrets → Federated
   credentials → Add → *GitHub Actions deploying Azure resources*):
   organisation `dragosdimitriu`, repository `pdf45`, entity type
   **Environment**, name **`signing`**.
3. **Role**: on the certificate profile (or the signing account) → Access
   control (IAM) → Add role assignment → *Artifact Signing Certificate Profile
   Signer* → the app registration.
4. **GitHub repository** → Settings:
   - Environments → New environment **`signing`** (optionally restrict it to
     `main` and `v*` tags).
   - Secrets and variables → Actions → **Secrets**:
     `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
   - Same page → **Variables**:
     `AZURE_SIGNING_ENDPOINT` = `https://neu.codesigning.azure.net`
     (North Europe; it must match the account's region, e.g.
     `https://plc.codesigning.azure.net` for Poland Central),
     `AZURE_SIGNING_ACCOUNT` = the signing account name,
     `AZURE_SIGNING_PROFILE` = the certificate profile name.

The next push builds signed binaries; the workflow's *Check signatures* step
fails the build if a file is not validly signed.

## Notes

- The publisher shown by Windows comes from the certificate. Keep
  `PUBLISHER` in `src/pdf45/__init__.py` (version resource of the exe) and
  `AppPublisher` in `packaging/windows/pdf45.iss` identical to it.
- Signing does not remove SmartScreen warnings immediately: reputation is
  built per publisher as the signed files are downloaded.
- Certificates are short-lived and rotated by Microsoft; signatures carry a
  timestamp, so they stay valid after the certificate expires.
