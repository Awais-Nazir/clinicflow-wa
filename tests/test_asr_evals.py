from clinicflow.asr_evals import word_errors


def test_word_errors_counts_substitutions_insertions_and_deletions():
    assert word_errors("Cancel it tomorrow", "Cancel it today.") == (1, 3)
    assert word_errors("کل بک کریں", "کل ہی بک کریں۔") == (1, 3)
    assert word_errors("Kal appointment book karein", "appointment karein") == (2, 4)
