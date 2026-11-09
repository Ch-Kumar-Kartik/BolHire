# BolHire: a voice-first job assistant for India

## The problem
Job search in India still runs on forms and filters. Many candidates, especially those more comfortable speaking than typing, or working in a language other than English, drop out before they finish a profile.

## The solution
BolHire lets a candidate describe themselves out loud. It turns that conversation into a structured profile, and matches it to real job listings with a transparent score that shows why each job fits.

## How it works
1. **Speak.** The candidate talks for under a minute. Gnani's speech-to-text transcribes it, and Gnani's text-to-speech reads the assistant's reply back.
2. **Extract.** An LLM pulls out role, experience, location and skills. Every field must be backed by something the candidate actually said, otherwise it's left blank.
3. **Consent and save.** The candidate reviews the profile, gets a recovery key, and confirms consent before anything is stored.
4. **Match.** A deterministic, rule-based scorer ranks jobs on skills, role, experience and location, and explains each score. There is no black-box model.
5. **Fetch live listings.** With one click, BolHire fetches live listings for the saved profile through Apify, cleans and matches them, and shows the source, the retrieval date and a link to the original posting.

## What is built (demo scope)
- Voice intake in English, using Gnani speech models
- Consent-first profile storage with a recovery key
- Explainable job matching, with a catalogue of sample jobs and live fetching of real listings
- A responsive web app (React and FastAPI, PostgreSQL)

## Principles
Matching is rule-based, not AI-generated. Nothing is sent to an employer. BolHire never applies to jobs for the candidate.

## Known limits and next steps
- English only so far. Hindi and other Indian languages are next.
- Live listings are fetched through Apify for a private local demo. A public product would use authorized job sources, such as employers' own job feeds.
- Listings carry no years-of-experience field, so experience scoring is lenient.
- Next: CV upload, a recruiter side, and stronger role matching.