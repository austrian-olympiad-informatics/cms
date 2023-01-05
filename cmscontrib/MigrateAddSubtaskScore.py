#!/usr/bin/env python3

from cms.db import Session, SubmissionResult, SubtaskScore
session = Session()

def convert_submission_result(submission_result):
    submission_result.subtask_scores = []
    if (
        submission_result.score_details is not None and
        # did not compile
        not (submission_result.score_details == [] and submission_result.score == 0)
    ):
        st_datas = None
        try:
            st_datas = {
                st["idx"]: st["max_score"] * st["score_fraction"]
                for st in submission_result.score_details
            }
        except (KeyError, ValueError):
            pass

        if not st_datas:
            # Task's score type is not group, assume a single subtask.
            st_datas = {1: submission_result.score}

        for idx, score in st_datas.items():
            st_score = SubtaskScore(
                submission_id=submission_result.submission_id,
                dataset_id=submission_result.dataset_id,
                subtask_idx=idx,
                score=score,
            )
            submission_result.subtask_scores.append(st_score)


srs = session.query(SubmissionResult).all()
for sr in srs:
    convert_submission_result(sr)
session.commit()
