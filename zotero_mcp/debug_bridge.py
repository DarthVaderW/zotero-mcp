"""Local Zotero Debug Bridge transport and data helpers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from zotero_mcp.config import (
    DEBUG_BRIDGE_LIBRARY_ID,
    DEBUG_BRIDGE_TOKEN,
    DEBUG_BRIDGE_URL,
)
from zotero_mcp.validators import require_item_type

def ensure_debug_bridge() -> None:
    if not DEBUG_BRIDGE_TOKEN:
        raise RuntimeError(
            "Error: ZOTERO_DEBUG_BRIDGE_TOKEN is required for this command\n"
            "Set it from your local Zotero debug-bridge plugin."
        )

def debug_bridge(js_code: str):
    req = urllib.request.Request(
        DEBUG_BRIDGE_URL,
        data=js_code.encode("utf-8"),
        headers={
            "Content-Type": "text/plain",
            "Authorization": f"Bearer {DEBUG_BRIDGE_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"Debug-bridge HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Debug-bridge network error: {e.reason}") from e

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body

def db_ping():
    return debug_bridge("""
await Zotero.Schema.schemaUpdatePromise;
return Zotero.version;
""")

def db_get_items(limit=100, collection_key=None):
    if collection_key:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const collection = Zotero.Collections.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(collection_key)});
if (!collection) throw new Error("Collection not found");
const items = await collection.getChildItems(false, false);
return items.slice(0, {int(limit)}).map(i => ({{
  key: i.key,
  itemType: Zotero.ItemTypes.getName(i.itemTypeID),
  title: i.getDisplayTitle(),
  creators: i.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: i.dateAdded,
  dateModified: i.dateModified
}}));
"""
    else:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const items = await Zotero.Items.getAll({DEBUG_BRIDGE_LIBRARY_ID}, false, ["itemType", "title", "creators", "dateAdded", "dateModified"]);
return items.slice(0, {int(limit)}).map(i => ({{
  key: i.key,
  itemType: Zotero.ItemTypes.getName(i.itemTypeID),
  title: i.getDisplayTitle(),
  creators: i.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: i.dateAdded,
  dateModified: i.dateModified
}}));
"""
    return debug_bridge(js)

def db_search(query, limit=50):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const search = new Zotero.Search();
search.libraryID = {DEBUG_BRIDGE_LIBRARY_ID};
search.addCondition("quicksearch", "contains", {json.dumps(query)});
const itemIDs = await search.search();
const items = [];
for (const id of itemIDs.slice(0, {int(limit)})) {{
  const item = Zotero.Items.get(id);
  if (!item) continue;
  items.push({{
    key: item.key,
    itemType: Zotero.ItemTypes.getName(item.itemTypeID),
    title: item.getDisplayTitle(),
    creators: item.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
    dateAdded: item.dateAdded
  }});
}}
return items;
"""
    return debug_bridge(js)

def db_get_item(key):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return null;
return {{
  key: item.key,
  itemType: Zotero.ItemTypes.getName(item.itemTypeID),
  title: item.getDisplayTitle(),
  creators: item.getCreators().map(c => c.fieldMode === 1 ? c.lastName : ((c.firstName || "") + " " + (c.lastName || "")).trim()).filter(Boolean).join(", "),
  dateAdded: item.dateAdded,
  dateModified: item.dateModified,
  DOI: item.getField("DOI"),
  url: item.getField("url"),
  abstractNote: item.getField("abstractNote")
}};
"""
    return debug_bridge(js)

def db_get_children(key):
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return [];
const result = [];
for (const id of item.getAttachments()) {{
  const att = Zotero.Items.get(id);
  if (!att) continue;
  result.push({{ key: att.key, itemType: "attachment", title: att.getDisplayTitle() || "Attachment", contentType: att.getField("contentType") }});
}}
for (const id of item.getNotes()) {{
  const note = Zotero.Items.get(id);
  if (!note) continue;
  result.push({{ key: note.key, itemType: "note", title: note.getDisplayTitle() || "Note" }});
}}
return result;
"""
    return debug_bridge(js)

def db_get_collections():
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const collections = Zotero.Collections.getByLibrary({DEBUG_BRIDGE_LIBRARY_ID});
return collections.slice(0, 200).map(c => ({{ key: c.key, name: c.getDisplayTitle(), dateAdded: c.dateAdded }}));
""")

def db_get_tags():
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const tags = await Zotero.Tags.getAll({DEBUG_BRIDGE_LIBRARY_ID});
return tags.slice(0, 200).map(t => ({{ name: t.tag, type: t.type }}));
""")

def db_create_item(item_data):
    payload = dict(item_data)
    require_item_type(payload)
    payload.setdefault("title", "")
    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = new Zotero.Item({json.dumps(payload.get("itemType"))});
item.libraryID = {DEBUG_BRIDGE_LIBRARY_ID};
const data = {json.dumps(payload)};
for (const [k, v] of Object.entries(data)) {{
  if (k === "itemType" || k.startsWith("__") || v === null || v === undefined) continue;
  if (k === "creators" && Array.isArray(v)) {{ item.setCreators(v); continue; }}
  item.setField(k, v);
}}
await item.saveTx();
return {{ key: item.key, success: true }};
"""
    return debug_bridge(js)

def db_add_attachment(parent_key, file_path, title="Full Text PDF"):
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return {"success": False, "error": f"File not found: {abs_path}"}

    js = f"""
await Zotero.Schema.schemaUpdatePromise;
const parent = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(parent_key)});
if (!parent) throw new Error("Parent item not found");
const file = Zotero.File.pathToFile({json.dumps(abs_path)});
if (!file.exists()) throw new Error("File not found");
const att = await Zotero.Attachments.importFromFile({{ file, parentItemID: parent.id }});
if (att && {json.dumps(title)}) {{
  att.setField("title", {json.dumps(title)});
  await att.saveTx();
}}
return att ? att.key : null;
"""
    result = debug_bridge(js)
    return {"success": bool(result), "attachment_key": result} if result else {"success": False, "error": "Failed to import file"}

def db_add_snapshot(parent_key, page_url, title="Web Page Snapshot"):
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const parent = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(parent_key)});
if (!parent) throw new Error("Parent item not found");
const att = await Zotero.Attachments.importFromURL({{
  libraryID: {DEBUG_BRIDGE_LIBRARY_ID},
  url: {json.dumps(page_url)},
  parentItemID: parent.id,
  title: {json.dumps(title)},
  contentType: "text/html"
}});
return att ? att.key : null;
""")

def db_add_item_to_collection(item_key, collection_name_or_key):
    return debug_bridge(f"""
await Zotero.Schema.schemaUpdatePromise;
const lib = {DEBUG_BRIDGE_LIBRARY_ID};
const item = Zotero.Items.getByLibraryAndKey(lib, {json.dumps(item_key)});
if (!item) throw new Error("Item not found");
const target = {json.dumps(collection_name_or_key)};
let col = /^[A-Za-z0-9]{{8}}$/.test(target) ? Zotero.Collections.getByLibraryAndKey(lib, target) : null;
if (!col) col = Zotero.Collections.getByLibrary(lib).find(c => c.name === target) || null;
if (!col) {{
  col = new Zotero.Collection();
  col.libraryID = lib;
  col.name = target;
  await col.saveTx();
}}
item.addToCollection(col.key);
await item.saveTx();
return {{ itemKey: item.key, collectionKey: col.key, collectionName: col.name }};
""")

def db_delete_item(key, permanent=False):
    if permanent:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return {{ success: false, error: "Item not found" }};
await item.eraseTx();
return {{ success: true, mode: "permanent" }};
"""
    else:
        js = f"""
await Zotero.Schema.schemaUpdatePromise;
const item = Zotero.Items.getByLibraryAndKey({DEBUG_BRIDGE_LIBRARY_ID}, {json.dumps(key)});
if (!item) return {{ success: false, error: "Item not found" }};
item.deleted = true;
await item.saveTx();
return {{ success: true, mode: "trash" }};
"""
    return debug_bridge(js)
