-- ============================================================================
-- CMS production schema upgrade: f634bb68 (AOI, ~2025-05) -> merged cms-dev/main
-- ============================================================================
--
-- This upgrades the AOI production CMS database from the schema at commit
-- f634bb68 to the schema produced by the merge of upstream cms-dev/main.
--
-- It was derived by diffing a fresh cmsInitDB of f634bb68 against a fresh
-- cmsInitDB of the merged branch (per-column, via information_schema). The AOI
-- feature additions (session tokens, subtask scores, memes, SSO columns,
-- frontend v2) are ALREADY on prod and are intentionally NOT touched here.
--
-- The data transformations mirror CMS's own dump-updaters:
--   * groups / timing move  -> update_48.py
--   * *_sandbox column split -> update_46.py
--   * submissions.opaque_id  -> update_45.py
--
-- !!! DO NOT run blindly on prod. Test on a restored copy first:
--       createdb cmsdb_copy && pg_restore/psql < prod_dump.sql
--       psql cmsdb_copy -f upgrade_prod_f634bb68_to_main.sql
--     then start the merged CMS against the copy and smoke-test.
--
-- Everything runs in one transaction: on any error, nothing is applied.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. USER GROUPS
--    Upstream moved contest timing (start/stop, analysis mode, per_user_time)
--    out of `contests` into a per-contest `groups` row. Each contest gets one
--    "default" group; every participation joins its contest's default group.
-- ----------------------------------------------------------------------------

CREATE TABLE public.groups (
    id integer NOT NULL,
    name character varying NOT NULL,
    start timestamp without time zone NOT NULL,
    stop timestamp without time zone NOT NULL,
    analysis_enabled boolean NOT NULL,
    analysis_start timestamp without time zone NOT NULL,
    analysis_stop timestamp without time zone NOT NULL,
    per_user_time interval,
    contest_id integer NOT NULL,
    CONSTRAINT groups_check CHECK ((start <= stop)),
    CONSTRAINT groups_check1 CHECK ((stop <= analysis_start)),
    CONSTRAINT groups_check2 CHECK ((analysis_start <= analysis_stop)),
    CONSTRAINT groups_per_user_time_check CHECK ((per_user_time >= '00:00:00'::interval))
);

CREATE SEQUENCE public.groups_id_seq AS integer
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.groups_id_seq OWNED BY public.groups.id;
ALTER TABLE ONLY public.groups
    ALTER COLUMN id SET DEFAULT nextval('public.groups_id_seq'::regclass);

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_contest_id_name_key UNIQUE (contest_id, name);
ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_id_contest_id_key UNIQUE (id, contest_id);
CREATE INDEX ix_groups_contest_id ON public.groups USING btree (contest_id);
ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_contest_id_fkey FOREIGN KEY (contest_id)
    REFERENCES public.contests(id) ON UPDATE CASCADE ON DELETE CASCADE;

-- New FK columns on contests / participations.
ALTER TABLE public.contests ADD COLUMN main_group_id integer;
ALTER TABLE public.participations ADD COLUMN group_id integer;

-- Create one "default" group per contest, copying the timing across.
INSERT INTO public.groups
    (name, start, stop, analysis_enabled, analysis_start, analysis_stop,
     per_user_time, contest_id)
SELECT 'default', c.start, c.stop, c.analysis_enabled, c.analysis_start,
       c.analysis_stop, c.per_user_time, c.id
FROM public.contests c;

-- Point each contest at its default group.
UPDATE public.contests c
SET main_group_id = g.id
FROM public.groups g
WHERE g.contest_id = c.id AND g.name = 'default';

-- Assign every participation to its contest's default group.
UPDATE public.participations p
SET group_id = g.id
FROM public.groups g
WHERE g.contest_id = p.contest_id AND g.name = 'default';

-- Now the FK columns can be constrained.
ALTER TABLE public.participations ALTER COLUMN group_id SET NOT NULL;
CREATE INDEX ix_contests_main_group_id ON public.contests USING btree (main_group_id);
CREATE INDEX ix_participations_group_id ON public.participations USING btree (group_id);
ALTER TABLE ONLY public.contests
    ADD CONSTRAINT fk_contest_main_group_id FOREIGN KEY (main_group_id)
    REFERENCES public.groups(id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE ONLY public.participations
    ADD CONSTRAINT participations_group_id_fkey FOREIGN KEY (group_id)
    REFERENCES public.groups(id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE ONLY public.participations
    ADD CONSTRAINT participations_group_id_contest_id_fkey
    FOREIGN KEY (group_id, contest_id) REFERENCES public.groups(id, contest_id);

-- Finally, drop the timing columns from contests (now owned by groups).
ALTER TABLE public.contests
    DROP COLUMN start,
    DROP COLUMN stop,
    DROP COLUMN analysis_enabled,
    DROP COLUMN analysis_start,
    DROP COLUMN analysis_stop,
    DROP COLUMN per_user_time;

-- ----------------------------------------------------------------------------
-- 2. SANDBOX COLUMN SPLIT
--    `<x>_sandbox` (a single ':'-joined path string) became
--    `<x>_sandbox_paths` (text[]) plus a `<x>_sandbox_digests` (text[]) that is
--    NULL for historical rows (mirrors update_46.py, which only fills _paths).
-- ----------------------------------------------------------------------------

-- evaluations.evaluation_sandbox
ALTER TABLE public.evaluations
    ADD COLUMN evaluation_sandbox_paths character varying[],
    ADD COLUMN evaluation_sandbox_digests character varying[];
UPDATE public.evaluations SET evaluation_sandbox_paths =
    CASE WHEN evaluation_sandbox IS NULL THEN NULL
         WHEN evaluation_sandbox = '' THEN ARRAY[]::character varying[]
         ELSE string_to_array(evaluation_sandbox, ':') END;
ALTER TABLE public.evaluations DROP COLUMN evaluation_sandbox;

-- submission_results.compilation_sandbox
ALTER TABLE public.submission_results
    ADD COLUMN compilation_sandbox_paths character varying[],
    ADD COLUMN compilation_sandbox_digests character varying[];
UPDATE public.submission_results SET compilation_sandbox_paths =
    CASE WHEN compilation_sandbox IS NULL THEN NULL
         WHEN compilation_sandbox = '' THEN ARRAY[]::character varying[]
         ELSE string_to_array(compilation_sandbox, ':') END;
ALTER TABLE public.submission_results DROP COLUMN compilation_sandbox;

-- user_test_results.compilation_sandbox and .evaluation_sandbox
ALTER TABLE public.user_test_results
    ADD COLUMN compilation_sandbox_paths character varying[],
    ADD COLUMN compilation_sandbox_digests character varying[],
    ADD COLUMN evaluation_sandbox_paths character varying[],
    ADD COLUMN evaluation_sandbox_digests character varying[];
UPDATE public.user_test_results SET
    compilation_sandbox_paths =
        CASE WHEN compilation_sandbox IS NULL THEN NULL
             WHEN compilation_sandbox = '' THEN ARRAY[]::character varying[]
             ELSE string_to_array(compilation_sandbox, ':') END,
    evaluation_sandbox_paths =
        CASE WHEN evaluation_sandbox IS NULL THEN NULL
             WHEN evaluation_sandbox = '' THEN ARRAY[]::character varying[]
             ELSE string_to_array(evaluation_sandbox, ':') END;
ALTER TABLE public.user_test_results
    DROP COLUMN compilation_sandbox,
    DROP COLUMN evaluation_sandbox;

-- ----------------------------------------------------------------------------
-- 3. OTHER NEW UPSTREAM COLUMNS
-- ----------------------------------------------------------------------------

-- contests: new flags/intervals.
ALTER TABLE public.contests
    ADD COLUMN allow_unofficial_submission_before_analysis_mode boolean
        NOT NULL DEFAULT false,
    ADD COLUMN min_submission_interval_grace_period interval,
    ADD CONSTRAINT contests_min_submission_interval_grace_period_check
        CHECK ((min_submission_interval_grace_period > '00:00:00'::interval));
-- The column has no default in the target schema; drop it now that existing
-- rows are populated.
ALTER TABLE public.contests
    ALTER COLUMN allow_unofficial_submission_before_analysis_mode DROP DEFAULT;

-- evaluations: admin-only text.
ALTER TABLE public.evaluations ADD COLUMN admin_text character varying;

-- submission_results: when it was scored.
ALTER TABLE public.submission_results
    ADD COLUMN scored_at timestamp without time zone;

-- tasks: per-task language allow-list.
ALTER TABLE public.tasks ADD COLUMN allowed_languages character varying[];

-- submissions.opaque_id: user-visible id, unique per participation.
-- update_45.py assigns random ids; the submission's own (globally unique) id
-- trivially satisfies UNIQUE(participation_id, opaque_id). Replace with random
-- values here if you need them to be non-guessable.
ALTER TABLE public.submissions ADD COLUMN opaque_id bigint;
UPDATE public.submissions SET opaque_id = id;
ALTER TABLE public.submissions ALTER COLUMN opaque_id SET NOT NULL;
ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT participation_opaque_unique UNIQUE (participation_id, opaque_id);

-- ----------------------------------------------------------------------------
-- 4. DROP THE PRINTING SUBSYSTEM (removed upstream)
-- ----------------------------------------------------------------------------
DROP TABLE public.printjobs;

COMMIT;
