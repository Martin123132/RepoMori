from app import Store


def test_store_lists_saved_titles(tmp_path):
    store = Store(tmp_path / "notes.sqlite")
    store.save_note("alpha", "first")
    store.save_note("beta", "second")
    assert store.list_titles() == ["alpha", "beta"]
