# Project maintenance instructions

Read `使用说明.md` 的「本地改动记录」与 README 的功能说明 before changing this controller. It records what this fork changes relative to upstream and which behaviours are verified.

- Distinguish AI-course knowledge exams, shared-course video popups, and shared-course ordinary tests. Ordinary shared-course tests now have live validation for types 1/2/14 only (2026-10-24); do not generalize to other question types or final exams.
- Do not equate resource studyProgress with exam mastery or one successful exam with completion of the course. Report actual server results and limits of validation.
- Diagnose DNS, TCP, TLS, proxy, TUN and HTTP separately. Disabling HTTP proxy does not prove direct routing while TUN is active. Preserve certificate verification.
- Keep GUI/child process output UTF-8 and preserve specific exception causes and nonzero failure status.
- Never fall back to random answers. Check question type, option IDs, cardinality, blank order and nonempty content before saving. Fill blanks use TEXT, choice questions use IDs, including in caches and post-grading updates.
- Network timeout after a save can mean success: read back before retrying. Retry transient failures with a bound; do not blindly retry explicit server rejection or submit when saves remain uncertain. Stop exam heartbeat on all exits.
- Check current mastery before the retry ceiling. At the ceiling, record unmet knowledge and continue; report incomplete outcomes honestly.
- Never import the live logger for read-only probes/tests: its handlers truncate logs. New offline regression suite isolates methods via AST. Preserve original logs before real runs.
- Run: python -m unittest discover -s selftest -p test_ai_resilience.py. Legacy selftest scripts may exit on import; do not confuse that with new implementation failures.
- Back up edits; never print API keys/cookies or forward an old provider's key to another provider. Confirm model IDs against official provider documentation.
- Do not promise ongoing supervision without an actual authorized monitor. A locally running process continues independently of the chat.

- shared_tests.py has 21 offline tests plus live authentication/save/readback/submit/grade evidence for six ordinary shared-course tests. Captcha requires the human user; saves and submissions with uncertain outcomes stop. Preserve checkpoints and do not redo submitted work. Run: python -m unittest discover -s selftest -p test_shared_tests.py.

- AI answers must be atomically drafted before saving (ai_exam_drafts), separate from verified answer caches. Verify the question/version/options fingerprint before reuse; verified caches take priority; submitted drafts are closed. AI submit timeouts and explicit rejections must not trigger blind automatic resubmission. AI resilience suite now has 15 tests.
