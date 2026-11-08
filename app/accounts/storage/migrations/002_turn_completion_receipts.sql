-- Immutable completion identity for retry-safe conversation turns.
ALTER TABLE chat_turn_requests
ADD COLUMN completion_fingerprint CHAR(64);

ALTER TABLE chat_turn_requests
ADD COLUMN completed_revision BIGINT;

ALTER TABLE chat_turn_requests
ADD COLUMN assistant_message_id UUID;

ALTER TABLE chat_turn_requests
ADD CONSTRAINT chat_turn_completion_consistency
CHECK (
    status <> 'completed'
    OR (
        completion_fingerprint IS NOT NULL
        AND completed_revision IS NOT NULL
        AND assistant_message_id IS NOT NULL
    )
) NOT VALID;
