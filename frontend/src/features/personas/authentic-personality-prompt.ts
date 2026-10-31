/**
 * Copy-ready interview prompt used with the user's own ChatGPT or Claude.
 * Keep this as one canonical literal so the UI, clipboard action, and tests
 * cannot drift into different shortened versions.
 */
export const AUTHENTIC_PERSONALITY_PROMPT = `# ListingIQ — Personal AI Persona Builder

You are helping me create my **ListingIQ Personal AI Persona**, which will represent my communication style in automated outreach and ongoing conversations with property owners.

I will attach the official ListingIQ Persona Template and may provide notes, documents, professional information or examples of messages I've written.

Your job is to understand how I communicate, organize my professional knowledge and complete the template accurately.

## Your three deliverables

1. **Completed Persona Template** — A fully structured, import-compatible document that teaches ListingIQ how I naturally communicate.
2. **Knowledge Base Document** — A separate document containing factual information about my experience, brokerage, services and business for ListingIQ's RAG knowledge system.
3. **Missing Information** — A brief list of information I haven't provided or that remains unverified.

The Persona defines **how I communicate**. The Knowledge Base describes **what is true about my business**. Do not confuse the two.

## 1. Understand my information

First, read the attached template and everything I provide.

Extract useful information directly from my notes, documents and conversation examples. Do not ask questions about information I've already supplied.

If essential details are missing, ask **3–5 short, relevant questions at a time**, using natural language.

Cover these topics as necessary:

- **Identity:** Name, role, current brokerage, licensing and public professional contact information.
- **Communication DNA:** Personality, tone, formality, preferred response length, conversation style, greetings, sign-offs and language preferences.
- **Signature Phrases (Use):** Exact expressions I naturally use when communicating with clients.
- **Avoid:** Words, phrases, filler, exaggerations and sales tactics I dislike.
- **Professional Experience:** Residential sales experience separately from commercial, investment and business-sale experience.
- **Selling Approach:** Property assessment, pricing, marketing, negotiation, follow-up, capabilities and services.
- **Conversation Examples:** How I would naturally respond to common homeowner questions, objections and situations.
- **Knowledge:** Qualifications, track record, brokerage information, services, fees, processes and FAQs.
- **Human Handoff:** Situations requiring direct involvement from me or another agent.

Ask me to write sample responses in the exact words I would actually send to a homeowner.

**Do not prolong the interview unnecessarily.** Once sufficient information is available, generate the deliverables. If optional information is still missing, leave it blank and include it in the missing-information list.

## 2. Authenticity and accuracy rules

Follow these rules strictly:

1. Use only information supplied in this conversation or in the files I provide. Do not claim access to outside private conversations, accounts or records.
2. Never invent credentials, licenses, years of experience, transactions, sales figures, rankings, achievements, buyers, marketing capabilities, fees, service areas or business practices.
3. Preserve my natural wording and communication style. Do not rewrite everything into formal or generic AI language.
4. Keep residential and commercial/business experience separate. Experience selling businesses does not establish experience selling residential homes.
5. Give factual claims one of these statuses:
   - **Verified:** Supported by an actual identified record or document.
   - **Self-reported:** Information I personally provided without independent verification.
   - **Needs confirmation:** Missing, unclear or unsubstantiated information.
6. Do not treat self-reported information as independently verified or automatically approved for factual claims in outbound messages.
7. Treat signature phrases as examples of my voice, not proof of factual statements.
8. Label conversation examples as **Authentic**, **User-written** or **Illustrative**. Do not invent authentic messages. Clearly identify AI-generated examples as illustrative, never as approved or authentic.
9. Remove private customer names, phone numbers, addresses and other identifying information.
10. Do not invent missing answers to make the template appear complete.

## 3. Complete the attached Persona Template

Use the **exact headings, field labels and section order** from the attached ListingIQ template.

Do not rename, merge, omit or rearrange fields because ListingIQ uses them for importing.

Pay particular attention to:

- Communication DNA
- Signature phrases (Use)
- Avoid expressions
- Residential experience
- Commercial/business experience
- Conversation examples
- Selling Approach and Services
- Knowledge Base

Write every Signature Phrase and Avoid expression as a separate item in its designated field.

Only include phrases I supplied or explicitly adopted. Do not automatically insert marketing slogans.

Fill conversation examples with my actual natural language wherever available. If an answer is unknown, leave it blank rather than fabricating a response.

Remove drafting comments and internal AI instructions from completed fields.

Where a field genuinely does not apply, use **N/A with a short reason**. Where information is simply missing, leave it blank.

If the template also contains a Signature Phrases label within Knowledge Base, preserve that label for compatibility but do not duplicate stylistic phrases as factual RAG knowledge.

## 4. Create the separate Knowledge Base

Create a clean, structured document titled:

**Knowledge Base — [Agent Name]**

Organize factual information under:

- Identity and Licensing
- Brokerage and Team
- Residential Experience
- Commercial and Business-Sale Experience
- Services and Capabilities
- Selling Process
- Fees and Terms
- Frequently Asked Questions

Use one self-contained fact per entry, with:

**[Category | Source | Status] Fact**

Avoid duplication, unsupported claims, marketing language and unnecessary biography.

Do not include signature phrases, tone instructions or conversational examples here.

Information marked **Needs confirmation** should be excluded from usable knowledge and instead listed in the missing-information output.

## 5. Generate the final outputs directly

**Do not request a separate confirmation or approval before generating the completed documents.**

Once the available information has been collected:

1. Generate the completed Persona Template.
2. Generate the separate Knowledge Base.
3. Generate a short list of missing or unconfirmed information.

Do not present an additional review summary or ask me to confirm every field.

The documents are drafts intended to be imported and displayed in **ListingIQ's editable Persona form**. I can inspect, change, correct or complete any field there before saving or publishing.

The choice to edit is mine. Do not require another interview or approval cycle.

If file generation is supported, produce the completed Persona as a **DOCX Word document** using the supplied template. Otherwise, return structured Markdown preserving the exact section and field labels.

The Knowledge Base may be provided as a separate document.

Never claim that information is automatically verified, approved, submitted or published.

## 6. Communication quality

The completed Persona should enable ListingIQ to communicate like the actual agent:

- Natural, respectful and professional
- Short, precise and comprehensive
- Free from unnecessary filler or exaggerated empathy
- Context-aware, without repetitive questions
- Consistent with the agent's real vocabulary and expressions
- Honest about unknown information
- Free from invented promises or unsupported claims

Do not create a generic AI sales personality. Reproduce my individual communication style faithfully.

Platform consent, opt-out, disclosure and safety rules always remain applicable.

## Begin

First, confirm that you can read the attached ListingIQ Persona Template.

If I have already provided enough information, create the deliverables directly.

Otherwise, briefly summarize what you have and ask only the first 3–5 essential questions.

**Do not ask for a separate final review or confirmation.**`;
