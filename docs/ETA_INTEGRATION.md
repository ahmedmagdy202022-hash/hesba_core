# ETA e-invoicing: signing and sending (ETA-002)

EINV-001 builds the invoice document (type `i`, version 1.0) from a posted sales invoice and lists what is still missing. ETA-002 signs that document and sends it to the Egyptian Tax Authority. It then reads back whether it is valid, and can cancel it.

## What the client needs (once, on the authority's side)

1. **A taxpayer account on the e-invoicing portal** (invoicing.eta.gov.eg). It is opened with the tax registration number.
2. **An ERP system registered on the portal.** The portal gives it a **client ID** and **client secret**. The pre-production portal (preprod.invoicing.eta.gov.eg) gives a separate pair, for testing.
3. **An e-signature token** (a USB token from an accredited provider such as Egypt Trust or MCDR, or an HSM) carrying the company's signing certificate.
4. **A signer** running next to the token. It is a small program on the client's PC (or the HSM's service) that receives a document's text and returns its CAdES-BES signature (contract below).
5. **The company and invoice data:**
   - the 9-digit tax number in Company details;
   - the activity code and branch address in Settings → E-invoicing;
   - EGS/GS1 codes for items;
   - receiver data for business customers.

   The document page lists anything missing.

## Settings on the server (Render → Environment)

| Name | Value |
|---|---|
| `ETA_ENVIRONMENT` | `preprod` while testing, `prod` when live (set in the Render dashboard; the blueprint does not pin it, so a sync never switches a live client back to testing) |
| `ETA_CLIENT_ID` | the ERP system's client ID |
| `ETA_CLIENT_SECRET` | its secret (never typed into Hesba's screens or stored in the database) |
| `ETA_SIGNER_URL` | the signer's address, e.g. `https://signer.client-office.example/sign` |
| `ETA_SIGNER_TOKEN` | required: a shared secret the signer checks (sent as `Authorization: Bearer …`); Hesba never calls the signer without it |

After setting them, **Settings → E-invoicing → Test the connection** checks the portal login and the signer.

## Using it

On a posted sales invoice, open **E-invoice**. Once the data is complete, press **Sign and send**. Then:
- **Sent — being checked:** the portal accepted the submission and validates it in the background. Press **Refresh the status** after a minute.
- **Valid:** a link to the invoice's public page on the portal appears. A valid invoice can be **cancelled on the portal** with a reason, within the authority's allowed period.
- **Cancellation requested:** cancelling sends a request. For an invoice to a business, the receiver may decline it within the authority's window. Press **Refresh the status** to see the outcome: **Cancelled**, or **Valid** again if the receiver declined. The invoice cannot be sent again while a cancellation is pending.
- **Sending now:** shown while a sending is on its way. A second press (or a second user) is refused until the authority answers.
- **Rejected / Invalid:** the authority's reason is shown. Correct the data and send again; every sending is kept as history.

Sending never changes the invoice, its posting or any balance. The sending, the status checks and the cancellation are written to the audit log.

## The signer contract

```
POST {ETA_SIGNER_URL}
Authorization: Bearer {ETA_SIGNER_TOKEN}
Content-Type: application/json

{"serialized": "<the document's canonical text>"}

→ 200 {"signature": "<base64 CAdES-BES CMS signature>"}
```

The canonical text follows the authority's serialization rule (ETA SDK, "Document serialization"):
- a simple value is written exactly as it appears in the JSON sent, in double quotes: a string keeps its JSON escaping (a `"` inside a description is signed as `\"`, exactly as sent);
- an object property is its name in capitals, in quotes, followed by its value;
- an array writes its name once, then each element preceded by the name again.

Hesba computes it (`einvoice.portal.serialize`) from the exact document it sends, without the `signatures` property. The signer must sign that text as given: SHA-256, CAdES-BES with the signing-certificate-v2 attribute, as the authority requires. The ETA SDK's sample signer does exactly this with a USB token and can be wrapped behind this one endpoint.

## Not yet covered

- Credit and debit notes (document types `c` and `d`) for sales returns.
- Receipts (the separate e-receipt system for retail sales).
- Sending in batches or automatically on posting. Today it is one invoice at a time, by a person.
