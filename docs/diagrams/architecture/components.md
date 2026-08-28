# remarkable-spec · Components

```mermaid
classDiagram
    class formats {
        +parse_rm_file()
        +parse_metadata()
        +parse_content()
        +parse_pagedata()
        +load_document()
    }

    class Page {
        +rm_filename()
        +metadata_filename()
        +thumbnail_filename()
        +all_strokes()
        +rm_path()
    }

    class render {
        +render_page()
        +get_pen_renderer()
        +get_rgb()
        +rasterize_pdf_page()
    }

    class export {
        +export_svg()
        +export_png()
        +export_pdf()
    }

    class ocr {
        +render_rm_to_png()
        +transcribe_rm()
        +transcribe_page()
        +extract_mermaid_from_rm()
        +ocr_page()
    }

    class SyncManager {
        +pull_document()
        +sync_status()
        +sync_pull()
        +sync_push_file()
    }

    class DeviceConnection {
        +connect()
        +execute()
        +get_file()
        +put_file()
        +list_dir()
    }

    class SyncDB {
        +upsert_document()
        +upsert_page()
        +get_diagram()
        +put_diagram()
        +log_sync()
    }

    formats --> Page : produces
    render --> Page : reads
    export --> Page : reads
    export --> render : invokes
    ocr --> formats : invokes
    ocr --> export : invokes
    ocr --> Page : produces
    SyncManager --> DeviceConnection : invokes
    SyncManager --> SyncDB : writes

    note for formats "facade `src/remarkable_spec/formats/__init__.py:21-35`<br/>parse_rm_file `src/remarkable_spec/formats/rm_file.py:46`<br/>parse_metadata `src/remarkable_spec/formats/metadata.py:36`<br/>parse_content `src/remarkable_spec/formats/content.py:40`<br/>parse_pagedata `src/remarkable_spec/formats/pagedata.py:26`<br/>load_document `src/remarkable_spec/formats/document_loader.py:36`"

    note for Page "`src/remarkable_spec/models/page.py:99`<br/>rm_filename `:126` metadata_filename `:132` thumbnail_filename `:138`<br/>all_strokes `:144` rm_path `:148`"

    note for render "facade `src/remarkable_spec/render/__init__.py:37-54`<br/>render_page `src/remarkable_spec/render/engine.py:91`<br/>get_pen_renderer `src/remarkable_spec/render/pens.py:437`<br/>get_rgb `src/remarkable_spec/render/palette.py:41`<br/>rasterize_pdf_page `src/remarkable_spec/render/pdf_bg.py:15`"

    note for export "facade `src/remarkable_spec/export/__init__.py:20-24`<br/>export_svg `src/remarkable_spec/export/svg.py:18`<br/>export_png `src/remarkable_spec/export/png.py:19`<br/>export_pdf `src/remarkable_spec/export/pdf.py:19`"

    note for ocr "render_rm_to_png `src/remarkable_spec/ocr/pipeline.py:25` transcribe_rm `:86`<br/>transcribe_page `src/remarkable_spec/ocr/postprocess.py:110`<br/>extract_mermaid_from_rm `src/remarkable_spec/ocr/diagram.py:174`<br/>ocr_page `src/remarkable_spec/ocr/vision.py:140`"

    note for SyncManager "`src/remarkable_spec/device/sync.py:31`<br/>pull_document `:92` sync_status `:233` sync_pull `:303` sync_push_file `:456`"

    note for DeviceConnection "`src/remarkable_spec/device/connection.py:38`<br/>connect `:81` execute `:140` get_file `:166` put_file `:183` list_dir `:202`"

    note for SyncDB "`src/remarkable_spec/sync/db.py:26`<br/>upsert_document `:77` upsert_page `:132` get_diagram `:237` put_diagram `:253` log_sync `:278`"
```

## See also

- [module map](../../architecture/module-map.md) — 23 shared source citations
- [contract map](../../insights/contract-map.md) — 21 shared source citations
- [impact analysis](../../insights/impact-analysis.md) — 19 shared source citations
- [business logic](../../insights/business-logic.md) — 17 shared source citations
- [processes](../../behavior/processes.md) — 15 shared source citations
