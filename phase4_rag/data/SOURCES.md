# Phase 4 RAG Corpus — Sources

## Downloaded successfully (in this folder)
| File | Source | Original URL |
|---|---|---|
| dicgc_guide_to_deposit_insurance.md | DICGC | https://www.dicgc.org.in/guide-to-deposit-insurance |
| dicgc_faqs.md | DICGC | https://www.dicgc.org.in/FAQs |
| rbi_ombudsman_scheme_faqs.md | RBI | https://www.rbi.org.in/commonperson/english/scripts/FAQs.aspx?Id=3407 |
| rbi_kyc_master_direction.md | RBI | https://www.rbi.org.in/commonman/english/scripts/notification.aspx?id=2607 |

All four were fetched and converted to clean Markdown for chunking/embedding —
no PDF parsing needed in your pipeline.

## Blocked — need manual download
RBI's PDF file server (rbidocs.rbi.org.in) returns a CAPTCHA/bot-check page to
automated fetches. If you want the following as raw PDFs (e.g., for citing exact
page numbers), open these in a browser yourself and save them into this folder:

- **Master Circular on Customer Service in Banks (2015)**
  https://rbidocs.rbi.org.in/rdocs/notification/PDFs/59FM04072F58B1DD44DFADD486B9B0A59E9D.PDF
- **Full RB-IOS 2026 Scheme text** (the FAQ above is a good substitute, but this is the legal text)
  https://rbidocs.rbi.org.in/rdocs/content/pdfs/SCHEME16012026_A.pdf
- **Full KYC Master Direction PDF** (886 KB — the .md file here is condensed from the HTML version)
  https://www.rbi.org.in/commonman/Upload/English/Notification/PDFs/MD18KYCF6E92C82E1E1419D87323E3869BC9F13.pdf

## Important update (post-cutoff)
RB-IOS 2021 was superseded by **RB-IOS 2026** effective July 1, 2026. Complaints filed
before that date, and appeals related to them, are still handled under RB-IOS 2021.
Your grounding data reflects the current (2026) scheme.

## Usage note
These are public Indian government/regulatory documents — fine to use for chunking,
embedding, and citation in an educational RAG project. Don't republish full document
text elsewhere; keep citing back to the original RBI/DICGC pages.
