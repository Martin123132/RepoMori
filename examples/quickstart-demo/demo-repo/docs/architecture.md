# Architecture

The demo has one storage boundary. `Store.connect` owns sqlite connection creation, while `save_note` and `list_titles` use that boundary instead of opening their own database handles.
