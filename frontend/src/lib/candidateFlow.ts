import type { PreviewResult } from './candidateApi.ts'
import type { VoiceLanguage } from './voiceApi'

// State of the explicit save flow for ONE intake conversation. Sections of the UI appear cumulatively from these fields; nothing here is
// persisted by the browser (no localStorage): the recovery key lives only in this state until the save succeeds.
export interface FlowState {
  preview: PreviewResult | null
  recoveryKey: string | null
  keyRecorded: boolean
  consentAccepted: boolean
  busy: boolean
  done: { created: boolean } | null
  error: string | null
}

export type FlowAction =
  | { type: 'reset' }
  | { type: 'started' }
  | { type: 'previewLoaded'; preview: PreviewResult }
  | { type: 'keyIssued'; recoveryKey: string }
  | { type: 'keyRecordedChanged'; value: boolean }
  | { type: 'consentChanged'; value: boolean }
  | { type: 'saved'; created: boolean }
  | { type: 'failed'; message: string; discardKey?: boolean }

export const initialFlow: FlowState = {
  preview: null, recoveryKey: null, keyRecorded: false, consentAccepted: false, busy: false, done: null, error: null,
}

export function flowReducer(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case 'reset':
      return initialFlow
    case 'started':
      return { ...state, busy: true, error: null }
    case 'previewLoaded': {
      // Different content than the one the candidate agreed to: the earlier consent no longer applies.
      const changed = state.preview !== null && state.preview.profile_digest !== action.preview.profile_digest
      const noticeChanged = state.preview !== null && state.preview.consent.version !== action.preview.consent.version
      return { ...state, preview: action.preview, busy: false, error: null, consentAccepted: changed || noticeChanged ? false : state.consentAccepted }
    }
    case 'keyIssued':
      // A new key invalidates the old one on the server, so the earlier "I recorded it" and consent confirmations are void.
      return { ...state, recoveryKey: action.recoveryKey, keyRecorded: false, consentAccepted: false, busy: false, error: null }
    case 'keyRecordedChanged':
      return state.recoveryKey === null ? state : { ...state, keyRecorded: action.value }
    case 'consentChanged':
      return state.recoveryKey === null ? state : { ...state, consentAccepted: action.value }
    case 'saved':
      return { ...initialFlow, done: { created: action.created } }
    case 'failed':
      return action.discardKey
        ? { ...state, recoveryKey: null, keyRecorded: false, consentAccepted: false, busy: false, error: action.message }
        : { ...state, busy: false, error: action.message }
  }
}

export function canSave(state: FlowState): boolean {
  return !state.busy && state.done === null && state.preview !== null && state.preview.can_save
    && state.recoveryKey !== null && state.keyRecorded && state.consentAccepted
}

const MESSAGES: Record<string, string> = {
  SESSION_NOT_FOUND: 'This conversation is no longer available. If you already saved your profile, sign in with your recovery key below; otherwise start a new conversation.',
  SESSION_EXPIRED: 'This conversation expired. If you already saved your profile, sign in with your recovery key below; otherwise start a new conversation.',
  SESSION_WORKFLOW_MISMATCH: 'Profiles can only be saved from a Candidate Intake conversation.',
  PROFILE_EMPTY: 'There is nothing to save yet. Tell the assistant at least one detail about yourself first.',
  PROFILE_CHANGED: 'Your profile changed after you reviewed it. Review it again and confirm.',
  CONSENT_VERSION_OUTDATED: 'The consent notice changed. Read the updated notice and confirm again.',
  CONSENT_REQUIRED: 'You need to agree before the profile can be saved.',
  RECOVERY_KEY_NOT_ISSUED: 'That recovery key is no longer valid for this conversation. Create a new one.',
  RECOVERY_KEY_UNAVAILABLE: 'That recovery key cannot be used. Create a new one.',
  MALFORMED_RECOVERY_KEY: 'That recovery key does not look right. Check it and try again.',
  INVALID_RECOVERY_KEY: 'That recovery key was not recognised, or the profile has expired.',
  UNAUTHENTICATED: 'You are not signed in.',
  RATE_LIMITED: 'Too many attempts. Wait a moment and try again.',
  CANDIDATE_STORAGE_DISABLED: 'Saving profiles is not available on this server.',
  CANDIDATE_STORAGE_UNAVAILABLE: 'Profile storage is temporarily unavailable. Nothing was lost; try again shortly.',
  ORIGIN_NOT_ALLOWED: 'The request was blocked for safety. Open the app from its normal address and try again.',
  NETWORK_ERROR: 'Could not reach the server. Check that the backend is running.',
}

export function messageFor(code: string, fallback: string): string {
  return MESSAGES[code] ?? fallback
}

// Codes after which the key bound to this conversation is gone or unusable, so the candidate must request a new one.
export function discardsKey(code: string): boolean {
  return code === 'RECOVERY_KEY_NOT_ISSUED' || code === 'RECOVERY_KEY_UNAVAILABLE' || code === 'SESSION_NOT_FOUND' || code === 'SESSION_EXPIRED'
}

// Codes after which the same conversation can simply be reviewed again.
export function needsFreshPreview(code: string): boolean {
  return code === 'PROFILE_CHANGED' || code === 'CONSENT_VERSION_OUTDATED'
}

export const PROVIDER_NOTICE: Record<VoiceLanguage, { text: string; acknowledge: string }> = {
  'en-IN': {
    text: 'Candidate Intake sends your voice recording to the speech service and the AI language service configured for this app (for example Gnani and Google Gemini) so BolHire can understand you and build your profile. Nothing is stored in a candidate account unless you choose to save your profile later.',
    acknowledge: 'I understand',
  },
  'hi-IN': {
    text: 'कैंडिडेट इंटेक में आपकी आवाज़ की रिकॉर्डिंग इस ऐप में सेट की गई स्पीच सेवा और एआई भाषा सेवा (जैसे Gnani और Google Gemini) को भेजी जाती है, ताकि BolHire आपकी बात समझकर आपकी प्रोफ़ाइल बना सके। जब तक आप बाद में प्रोफ़ाइल सहेजना न चुनें, कैंडिडेट खाते में कुछ भी सहेजा नहीं जाता।',
    acknowledge: 'मैं समझता/समझती हूँ',
  },
}

export const CONSENT_LABEL: Record<VoiceLanguage, string> = { 'en-IN': 'I agree to the above', 'hi-IN': 'मैं ऊपर लिखी बातों से सहमत हूँ' }
