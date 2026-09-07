-- Sycophancy Dashboard — quick SQL queries for monitoring SEAL anti-sycophancy
-- Usage: docker exec seal-memory-db psql -U seal -d seal_memory -f sycophancy_dashboard.sql
-- Or: psql ... -c "<query>"

\echo '=== 1. Latest pi_score per agent (target: pi < 0.30) ==='
SELECT agent, eval_date, pi_score, tests_run, sycophantic_count, dissent_count, eval_method
FROM soul_v3.sycophancy_eval
WHERE eval_date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY eval_date DESC, agent;

\echo ''
\echo '=== 2. Pi trend last 14 days per agent ==='
SELECT agent, eval_date, pi_score
FROM soul_v3.sycophancy_eval
WHERE eval_date >= CURRENT_DATE - INTERVAL '14 days'
ORDER BY agent, eval_date DESC;

\echo ''
\echo '=== 3. Recent sycophantic instances (last 24h, risk >= 0.6) ==='
SELECT id, agent, evaluation, risk_score, flags,
       LEFT(user_input, 80) AS user_input_preview,
       LEFT(proposed_response, 80) AS response_preview,
       created_at
FROM soul_v3.sycophancy_log
WHERE created_at >= NOW() - INTERVAL '24 hours' AND risk_score >= 0.6
ORDER BY created_at DESC LIMIT 20;

\echo ''
\echo '=== 4. Dissent ratio per agent (last 7 days) ==='
SELECT
  agent,
  SUM(dissent_count) AS total_dissents,
  SUM(tests_run) AS total_tests,
  ROUND(SUM(dissent_count)::numeric / NULLIF(SUM(tests_run), 0), 3) AS dissent_ratio
FROM soul_v3.sycophancy_eval
WHERE eval_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY agent ORDER BY dissent_ratio DESC;

\echo ''
\echo '=== 5. Denial tracker — refusals last 24h ==='
SELECT agent, action_type, denial_reason, created_at
FROM soul_v3.denial_tracker
WHERE created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC LIMIT 20;

\echo ''
\echo '=== 6. Top sycophancy flags (frequency last 7 days) ==='
SELECT
  flag AS flag,
  COUNT(*) AS occurrences
FROM soul_v3.sycophancy_log,
     LATERAL jsonb_array_elements_text(flags) AS flag
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY flag
ORDER BY occurrences DESC LIMIT 15;
