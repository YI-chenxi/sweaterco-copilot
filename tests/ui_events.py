"""Native dialog events not yet exposed as AppTest widgets."""


def dismiss_workflow_dialog(app):
    # Send the same trigger used by the browser's native top-right close control.
    dialog = app.get("dialog")[0].proto.dialog
    states = app._tree.get_widget_states()
    states.widgets.add(id=dialog.id, trigger_value=True)
    return app._run(widget_state=states)


def analysis_copy_payloads(app):
    """Inspect the text/locale sent to each expanded answer's clipboard button."""
    import json

    return {
        item.proto.id.rsplit("-copy_analysis_", 1)[1]: json.loads(item.proto.json)
        for item in app.get("bidi_component")
        if item.proto.component_name == "sweaterco_copy_analysis"
    }
