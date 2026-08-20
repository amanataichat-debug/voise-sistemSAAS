-- Voicyfy Agent v2 migration
-- Adds onboarding documents, working hours, chat history to agent_configs
-- Adds agent_memory to contacts
-- Adds pre_call_response_id, post_call_decision, retry_count to tasks

ALTER TABLE agent_configs
  ADD COLUMN IF NOT EXISTS doc_who_am_i TEXT,
  ADD COLUMN IF NOT EXISTS doc_who_we_call TEXT,
  ADD COLUMN IF NOT EXISTS doc_how_we_talk TEXT,
  ADD COLUMN IF NOT EXISTS doc_what_we_offer TEXT,
  ADD COLUMN IF NOT EXISTS doc_rules_and_goals TEXT,
  ADD COLUMN IF NOT EXISTS working_hours_start INTEGER DEFAULT 9,
  ADD COLUMN IF NOT EXISTS working_hours_end INTEGER DEFAULT 21,
  ADD COLUMN IF NOT EXISTS chat_history JSONB DEFAULT '[]';

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS agent_memory JSONB DEFAULT '{}';

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS pre_call_response_id VARCHAR(255),
  ADD COLUMN IF NOT EXISTS post_call_decision VARCHAR(50),
  ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;

-- Update orchestrator_model to gpt-5 for existing records
UPDATE agent_configs SET orchestrator_model = 'gpt-5-2025-08-07';
ALTER TABLE agent_configs
  ALTER COLUMN orchestrator_model SET DEFAULT 'gpt-5-2025-08-07';

-- Index for faster PostCall polling
CREATE INDEX IF NOT EXISTS ix_tasks_pre_call_response
  ON tasks(pre_call_response_id)
  WHERE pre_call_response_id IS NOT NULL;
