export type VoiceWorkflow = 'general' | 'candidate_intake'

export interface ConversationSelection {
  workflow: VoiceWorkflow
  sessionId: string | null
}

export function selectWorkflow(
  current: ConversationSelection,
  workflow: VoiceWorkflow,
): ConversationSelection {
  return current.workflow === workflow ? current : { workflow, sessionId: null }
}
