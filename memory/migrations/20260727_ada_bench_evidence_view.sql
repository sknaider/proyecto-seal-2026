BEGIN;

CREATE OR REPLACE VIEW soul_v3.ada_evaluation_runs_v
WITH (security_barrier = true)
AS
SELECT id, suite_name, score, passed, run_at
FROM soul_v3.evaluation_runs
WHERE agent = 'ADA';

REVOKE ALL ON soul_v3.ada_evaluation_runs_v FROM PUBLIC;
GRANT SELECT ON soul_v3.ada_evaluation_runs_v TO login_ada_bridge;

COMMENT ON VIEW soul_v3.ada_evaluation_runs_v IS
    'Least-privilege replay surface for ADA SEAL-Bench evidence; excludes other agents and raw details.';

CREATE OR REPLACE VIEW soul_v3.ada_bench_runs_v
WITH (security_barrier = true)
AS
SELECT id, run_at, triggered_by, total_tests, passed, failed,
       score_avg, elapsed_ms, git_commit
FROM soul_v3.bench_runs
WHERE triggered_by IN ('manual_v5', 'ada_close_v5');

REVOKE ALL ON soul_v3.ada_bench_runs_v FROM PUBLIC;
GRANT SELECT ON soul_v3.ada_bench_runs_v TO login_ada_bridge;

CREATE OR REPLACE FUNCTION soul_v3.ada_persist_bench_v5(
    p_triggered_by text,
    p_results jsonb,
    p_elapsed_ms integer,
    p_git_commit text
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
DECLARE
    v_run_id bigint;
    v_total integer;
    v_passed integer;
    v_score numeric;
    v_row jsonb;
BEGIN
    IF p_triggered_by NOT IN ('manual_v5', 'ada_close_v5') THEN
        RAISE EXCEPTION 'invalid ADA bench trigger';
    END IF;
    IF jsonb_typeof(p_results) <> 'array' THEN
        RAISE EXCEPTION 'results must be a JSON array';
    END IF;

    v_total := jsonb_array_length(p_results);
    IF v_total < 1 OR v_total > 16 THEN
        RAISE EXCEPTION 'invalid ADA bench result count';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_results) AS item
        WHERE coalesce(item->>'category', '') NOT LIKE 'v5.%'
           OR (item->>'score')::numeric < 0
           OR (item->>'score')::numeric > 100
    ) THEN
        RAISE EXCEPTION 'invalid ADA bench result payload';
    END IF;

    SELECT count(*) FILTER (WHERE (item->>'passed')::boolean),
           round(avg((item->>'score')::numeric), 2)
      INTO v_passed, v_score
      FROM jsonb_array_elements(p_results) AS item;

    INSERT INTO soul_v3.bench_runs
        (triggered_by, total_tests, passed, failed, score_avg, elapsed_ms, git_commit)
    VALUES
        (p_triggered_by, v_total, v_passed, v_total - v_passed,
         v_score, greatest(p_elapsed_ms, 0), left(p_git_commit, 64))
    RETURNING id INTO v_run_id;

    FOR v_row IN SELECT value FROM jsonb_array_elements(p_results)
    LOOP
        INSERT INTO soul_v3.bench_results
            (run_id, category, test_name, passed, score, elapsed_ms, detail, error)
        VALUES
            (v_run_id,
             left(v_row->>'category', 255),
             left(v_row->>'test_name', 255),
             (v_row->>'passed')::boolean,
             (v_row->>'score')::numeric,
             greatest(coalesce((v_row->>'elapsed_ms')::integer, 0), 0),
             left(coalesce((v_row->'detail')::text, '{}'), 4000),
             left(v_row->>'error', 2000));
    END LOOP;
    RETURN v_run_id;
END;
$$;

REVOKE ALL ON FUNCTION soul_v3.ada_persist_bench_v5(text, jsonb, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.ada_persist_bench_v5(text, jsonb, integer, text)
    TO login_ada_bridge;

COMMIT;
