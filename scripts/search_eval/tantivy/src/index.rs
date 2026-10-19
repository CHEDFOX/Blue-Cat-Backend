//! Index layout and the streaming, resumable builder.
//!
//! One document per passage, one index for all jurisdictions. Text fields (pre-tokenized: the
//! token stream is computed here, never by a Tantivy tokenizer):
//! * `all`: every lexeme at its PostgreSQL position (context = weight B first, then text =
//!   weight A), with positions, no fieldnorms: used for MATCHING, so phrase distances behave like
//!   tsquery `<->`/`<N>`, including phrases that run from the context into the text;
//! * `a` / `b`: the weight-A (text) / weight-B (context) lexemes, frequencies only: used for
//!   BM25 RANKING (A x 1.0 + B x 0.4).
//!
//! Fast fields hold every predicate and ranking input of search._candidates/_classify.
//! Builder input: JSON lines on stdin (see `Row`), produced by ulis.retrieval.tantivy_engine.

use crate::analyzer;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::io::BufRead;
use std::path::Path;
use std::time::Instant;
use tantivy::schema::{
    Field, IndexRecordOption, NumericOptions, Schema, TextFieldIndexing, TextOptions, STRING,
};
use tantivy::tokenizer::{PreTokenizedString, Token};
use tantivy::{Index, IndexWriter, TantivyDocument};

pub const SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy)]
pub struct Fields {
    pub all: Field,
    pub a: Field,
    pub b: Field,
    pub id_hi: Field,
    pub id_lo: Field,
    pub pv_hi: Field,
    pub pv_lo: Field,
    pub jp_hi: Field,
    pub jp_lo: Field,
    pub inst_hi: Field,
    pub inst_lo: Field,
    pub judg_hi: Field,
    pub judg_lo: Field,
    pub kind: Field,
    pub status: Field,
    pub language: Field,
    pub search_config: Field,
    pub jurisdiction_id: Field,
    pub doc_type: Field,
    pub extent: Field,
    pub extent_empty: Field,
    pub valid_from: Field,
    pub valid_to: Field,
    pub has_valid_from: Field,
    pub has_valid_to: Field,
    pub text_length: Field,
}

pub fn schema() -> Schema {
    let mut b = Schema::builder();
    let all = TextOptions::default().set_indexing_options(
        TextFieldIndexing::default()
            .set_tokenizer("raw")
            .set_index_option(IndexRecordOption::WithFreqsAndPositions)
            .set_fieldnorms(false),
    );
    let weighted = TextOptions::default().set_indexing_options(
        TextFieldIndexing::default()
            .set_tokenizer("raw")
            .set_index_option(IndexRecordOption::WithFreqs)
            .set_fieldnorms(true),
    );
    b.add_text_field("all", all);
    b.add_text_field("a", weighted.clone());
    b.add_text_field("b", weighted);
    let fast = || NumericOptions::default().set_fast();
    for name in ["id_hi", "id_lo", "pv_hi", "pv_lo", "jp_hi", "jp_lo", "inst_hi", "inst_lo", "judg_hi", "judg_lo"] {
        b.add_u64_field(name, fast());
    }
    let fast_str = TextOptions::default().set_fast(None);
    for name in ["kind", "status", "language", "jurisdiction_id", "doc_type", "extent"] {
        b.add_text_field(name, fast_str.clone());
    }
    // indexed too: every config's query is restricted to its passages with a term query
    b.add_text_field("search_config", STRING | tantivy::schema::FAST);
    b.add_bool_field("extent_empty", fast());
    b.add_i64_field("valid_from", fast());
    b.add_i64_field("valid_to", fast());
    b.add_bool_field("has_valid_from", fast());
    b.add_bool_field("has_valid_to", fast());
    b.add_u64_field("text_length", fast());
    b.build()
}

pub fn fields(schema: &Schema) -> Fields {
    let f = |n: &str| schema.get_field(n).unwrap_or_else(|_| panic!("index lacks field {n}"));
    Fields {
        all: f("all"),
        a: f("a"),
        b: f("b"),
        id_hi: f("id_hi"),
        id_lo: f("id_lo"),
        pv_hi: f("pv_hi"),
        pv_lo: f("pv_lo"),
        jp_hi: f("jp_hi"),
        jp_lo: f("jp_lo"),
        inst_hi: f("inst_hi"),
        inst_lo: f("inst_lo"),
        judg_hi: f("judg_hi"),
        judg_lo: f("judg_lo"),
        kind: f("kind"),
        status: f("status"),
        language: f("language"),
        search_config: f("search_config"),
        jurisdiction_id: f("jurisdiction_id"),
        doc_type: f("doc_type"),
        extent: f("extent"),
        extent_empty: f("extent_empty"),
        valid_from: f("valid_from"),
        valid_to: f("valid_to"),
        has_valid_from: f("has_valid_from"),
        has_valid_to: f("has_valid_to"),
        text_length: f("text_length"),
    }
}

pub fn parse_uuid(s: &str) -> Result<u128, String> {
    let hex: String = s.chars().filter(|c| *c != '-').collect();
    if hex.len() != 32 {
        return Err(format!("bad uuid {s:?}"));
    }
    u128::from_str_radix(&hex, 16).map_err(|_| format!("bad uuid {s:?}"))
}

pub fn format_uuid(v: u128) -> String {
    let h = format!("{v:032x}");
    format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32])
}

/// One passage as streamed by the Python builder.
#[derive(Deserialize)]
pub struct Row {
    pub id: String,
    pub kind: String,
    pub pv: Option<String>,
    pub jp: Option<String>,
    pub inst: Option<String>,
    pub judg: Option<String>,
    pub jur: String,
    pub doc_type: String,
    pub lang: String,
    pub cfg: String,
    pub status: String,
    /// valid_from / valid_to as days since 1970-01-01, null for NULL.
    pub vf: Option<i64>,
    pub vt: Option<i64>,
    /// null for a NULL array (cardinality NULL: neither empty nor overlapping).
    pub extent: Option<Vec<String>>,
    /// length(p.text) in characters.
    pub len: u64,
    /// mode "pg": p.tsv::text.
    pub tsv: Option<String>,
    /// mode "native": coalesce(search_context, context, '') and coalesce(search_text, text).
    pub ctx: Option<String>,
    pub txt: Option<String>,
}

/// Parses tsvector output (`'lex':1A,5B 'it''s':3`): lexeme -> [(position, is_weight_a)].
pub fn parse_tsvector(text: &str) -> Result<Vec<(String, analyzer::WeightedPositions)>, String> {
    let s = text.as_bytes();
    let mut i = 0;
    let mut out = Vec::new();
    while i < s.len() {
        if s[i] == b' ' {
            i += 1;
            continue;
        }
        if s[i] != b'\'' {
            return Err(format!("tsvector: expected quote at byte {i}"));
        }
        i += 1;
        let mut lex = Vec::new();
        loop {
            match s.get(i) {
                None => return Err("tsvector: unterminated lexeme".into()),
                Some(b'\'') if s.get(i + 1) == Some(&b'\'') => {
                    lex.push(b'\'');
                    i += 2;
                }
                Some(b'\'') => {
                    i += 1;
                    break;
                }
                Some(b'\\') if i + 1 < s.len() => {
                    lex.push(s[i + 1]);
                    i += 2;
                }
                Some(&c) => {
                    lex.push(c);
                    i += 1;
                }
            }
        }
        let mut positions = Vec::new();
        if s.get(i) == Some(&b':') {
            i += 1;
            loop {
                let start = i;
                while i < s.len() && s[i].is_ascii_digit() {
                    i += 1;
                }
                let pos: u32 = std::str::from_utf8(&s[start..i]).unwrap().parse().map_err(|_| "tsvector: bad position")?;
                // weight letters: A = text, B = context (C/D do not occur in passages.tsv)
                let mut is_a = false;
                if i < s.len() && matches!(s[i], b'A' | b'B' | b'C' | b'D') {
                    is_a = s[i] == b'A';
                    i += 1;
                }
                positions.push((pos, is_a));
                if s.get(i) == Some(&b',') {
                    i += 1;
                } else {
                    break;
                }
            }
        }
        out.push((String::from_utf8(lex).map_err(|e| e.to_string())?, positions));
    }
    Ok(out)
}

fn pretokenized(mut tokens: Vec<(u32, String)>) -> PreTokenizedString {
    tokens.sort();
    PreTokenizedString {
        text: String::new(),
        tokens: tokens
            .into_iter()
            .map(|(pos, text)| Token { offset_from: 0, offset_to: 0, position: pos as usize, text, position_length: 1 })
            .collect(),
    }
}

pub fn make_doc(f: &Fields, mode: &str, row: &Row) -> Result<TantivyDocument, String> {
    let lexemes: Vec<(String, analyzer::WeightedPositions)> = match mode {
        "pg" => parse_tsvector(row.tsv.as_deref().ok_or("mode pg needs \"tsv\"")?)?,
        _ => {
            let cfg = analyzer::config(&row.cfg);
            let tv: BTreeMap<String, analyzer::WeightedPositions> = analyzer::passage_tsvector(
                cfg,
                row.ctx.as_deref().unwrap_or(""),
                row.txt.as_deref().ok_or("mode native needs \"txt\"")?,
            );
            tv.into_iter().collect()
        }
    };
    let (mut all, mut a, mut b) = (Vec::new(), Vec::new(), Vec::new());
    for (lex, positions) in lexemes {
        for (pos, is_a) in positions {
            all.push((pos, lex.clone()));
            if is_a {
                a.push((pos, lex.clone()));
            } else {
                b.push((pos, lex.clone()));
            }
        }
    }
    let mut doc = TantivyDocument::default();
    doc.add_pre_tokenized_text(f.all, pretokenized(all));
    doc.add_pre_tokenized_text(f.a, pretokenized(a));
    doc.add_pre_tokenized_text(f.b, pretokenized(b));
    let id = parse_uuid(&row.id)?;
    doc.add_u64(f.id_hi, (id >> 64) as u64);
    doc.add_u64(f.id_lo, id as u64);
    for (v, hi, lo) in [
        (&row.pv, f.pv_hi, f.pv_lo),
        (&row.jp, f.jp_hi, f.jp_lo),
        (&row.inst, f.inst_hi, f.inst_lo),
        (&row.judg, f.judg_hi, f.judg_lo),
    ] {
        if let Some(s) = v {
            let u = parse_uuid(s)?;
            doc.add_u64(hi, (u >> 64) as u64);
            doc.add_u64(lo, u as u64);
        }
    }
    doc.add_text(f.kind, &row.kind);
    doc.add_text(f.status, &row.status);
    doc.add_text(f.language, &row.lang);
    doc.add_text(f.search_config, &row.cfg);
    doc.add_text(f.jurisdiction_id, &row.jur);
    doc.add_text(f.doc_type, &row.doc_type);
    match &row.extent {
        Some(ext) => {
            doc.add_bool(f.extent_empty, ext.is_empty());
            for e in ext {
                doc.add_text(f.extent, e);
            }
        }
        None => doc.add_bool(f.extent_empty, false),
    }
    doc.add_bool(f.has_valid_from, row.vf.is_some());
    doc.add_bool(f.has_valid_to, row.vt.is_some());
    if let Some(v) = row.vf {
        doc.add_i64(f.valid_from, v);
    }
    if let Some(v) = row.vt {
        doc.add_i64(f.valid_to, v);
    }
    doc.add_u64(f.text_length, row.len);
    Ok(doc)
}

pub fn payload(index: &Index) -> serde_json::Value {
    index
        .load_metas()
        .ok()
        .and_then(|m| m.payload)
        .and_then(|p| serde_json::from_str(&p).ok())
        .unwrap_or(serde_json::Value::Null)
}

pub fn dir_size(path: &Path) -> u64 {
    std::fs::read_dir(path)
        .map(|rd| rd.filter_map(|e| e.ok()).filter_map(|e| e.metadata().ok()).filter(|m| m.is_file()).map(|m| m.len()).sum())
        .unwrap_or(0)
}

pub struct BuildOptions {
    pub heap_mb: usize,
    pub threads: usize,
    pub commit_every: u64,
    pub merge: bool,
    pub progress: bool,
}

/// Streams JSON rows from stdin into the index at `path` (created or resumed).
pub fn build(path: &Path, mode: &str, opts: &BuildOptions) -> Result<serde_json::Value, String> {
    if mode != "pg" && mode != "native" {
        return Err(format!("unknown mode {mode:?}"));
    }
    std::fs::create_dir_all(path).map_err(|e| e.to_string())?;
    let index = if path.join("meta.json").exists() {
        let index = Index::open_in_dir(path).map_err(|e| e.to_string())?;
        let p = payload(&index);
        if let Some(m) = p.get("mode").and_then(|v| v.as_str()) {
            if m != mode {
                return Err(format!("index at {} was built in mode {m}, not {mode}", path.display()));
            }
        }
        index
    } else {
        Index::create_in_dir(path, schema()).map_err(|e| e.to_string())?
    };
    let f = fields(&index.schema());
    let prior = payload(&index);
    let mut docs: u64 = prior.get("docs").and_then(|v| v.as_u64()).unwrap_or(0);
    let mut last_id: Option<String> = prior.get("last_id").and_then(|v| v.as_str()).map(String::from);
    let mut writer: IndexWriter<TantivyDocument> = index
        .writer_with_num_threads(opts.threads.max(1), opts.heap_mb * 1_000_000)
        .map_err(|e| e.to_string())?;
    if opts.merge {
        // one explicit merge at the end; background merges would race with it
        writer.set_merge_policy(Box::new(tantivy::indexer::NoMergePolicy));
    }
    let t0 = Instant::now();
    let mut added: u64 = 0;
    let commit = |writer: &mut IndexWriter<TantivyDocument>, docs: u64, last_id: &Option<String>, done: bool| -> Result<(), String> {
        let payload = serde_json::json!({
            "mode": mode, "docs": docs, "last_id": last_id, "complete": done, "schema": SCHEMA_VERSION,
        })
        .to_string();
        let mut prepared = writer.prepare_commit().map_err(|e| e.to_string())?;
        prepared.set_payload(&payload);
        prepared.commit().map_err(|e| e.to_string())?;
        Ok(())
    };
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let row: Row = serde_json::from_str(&line).map_err(|e| format!("bad row: {e}"))?;
        if let Some(prev) = &last_id {
            if row.id.as_str() <= prev.as_str() {
                return Err(format!("rows must arrive in ascending id order ({} after {prev})", row.id));
            }
        }
        let doc = make_doc(&f, mode, &row)?;
        writer.add_document(doc).map_err(|e| e.to_string())?;
        docs += 1;
        added += 1;
        last_id = Some(row.id);
        if added % opts.commit_every == 0 {
            commit(&mut writer, docs, &last_id, false)?;
            if opts.progress {
                eprintln!("committed {docs} docs ({:.0}s, last id {})", t0.elapsed().as_secs_f64(), last_id.as_deref().unwrap_or(""));
            }
        }
    }
    commit(&mut writer, docs, &last_id, true)?;
    let index_secs = t0.elapsed().as_secs_f64();
    if opts.merge {
        let ids = index.searchable_segment_ids().map_err(|e| e.to_string())?;
        if ids.len() > 1 {
            writer.merge(&ids).wait().map_err(|e| e.to_string())?;
        }
    }
    writer.wait_merging_threads().map_err(|e| e.to_string())?;
    if opts.merge {
        let w: IndexWriter<TantivyDocument> = index.writer_with_num_threads(1, 50_000_000).map_err(|e| e.to_string())?;
        w.garbage_collect_files().wait().map_err(|e| e.to_string())?;
    }
    Ok(serde_json::json!({
        "mode": mode, "docs_total": docs, "docs_added": added, "index_seconds": index_secs,
        "total_seconds": t0.elapsed().as_secs_f64(), "bytes": dir_size(path),
        "segments": index.searchable_segment_ids().map(|v| v.len()).unwrap_or(0),
    }))
}

/// Merges all segments into one and removes unused files.
pub fn merge(path: &Path) -> Result<serde_json::Value, String> {
    let index = Index::open_in_dir(path).map_err(|e| e.to_string())?;
    let mut writer: IndexWriter<TantivyDocument> = index.writer_with_num_threads(1, 200_000_000).map_err(|e| e.to_string())?;
    let ids = index.searchable_segment_ids().map_err(|e| e.to_string())?;
    let t0 = Instant::now();
    if ids.len() > 1 {
        writer.merge(&ids).wait().map_err(|e| e.to_string())?;
    }
    writer.garbage_collect_files().wait().map_err(|e| e.to_string())?;
    writer.wait_merging_threads().map_err(|e| e.to_string())?;
    Ok(serde_json::json!({"seconds": t0.elapsed().as_secs_f64(), "bytes": dir_size(path)}))
}

/// Document count, build payload, size on disk and per-field space usage.
pub fn info(path: &Path) -> Result<serde_json::Value, String> {
    let index = Index::open_in_dir(path).map_err(|e| e.to_string())?;
    let reader = index.reader().map_err(|e| e.to_string())?;
    let searcher = reader.searcher();
    let usage = searcher.space_usage().map_err(|e| e.to_string())?;
    let mut per_field: BTreeMap<String, BTreeMap<&str, u64>> = BTreeMap::new();
    for seg in usage.segments() {
        for (part, pf) in [
            ("termdict", seg.termdict()),
            ("postings", seg.postings()),
            ("positions", seg.positions()),
            ("fast_fields", seg.fast_fields()),
            ("fieldnorms", seg.fieldnorms()),
        ] {
            for fu in pf.fields() {
                let name = fu.field_name().to_string();
                *per_field.entry(name).or_default().entry(part).or_default() += fu.total().get_bytes();
            }
        }
    }
    Ok(serde_json::json!({
        "docs": searcher.num_docs(),
        "segments": searcher.segment_readers().len(),
        "payload": payload(&index),
        "bytes": dir_size(path),
        "space_usage_total": usage.total().get_bytes(),
        "per_field": per_field,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tsvector_text_parses() {
        let tv = parse_tsvector("'it''s':3A 'a\\\\b':1B,2 'part':1B,4A").unwrap();
        assert_eq!(tv[0], ("it's".to_string(), vec![(3, true)]));
        assert_eq!(tv[1], ("a\\b".to_string(), vec![(1, false), (2, false)]));
        assert_eq!(tv[2], ("part".to_string(), vec![(1, false), (4, true)]));
    }

    #[test]
    fn uuid_roundtrip() {
        let s = "41987a8e-63fa-44df-ac83-4555d227e45a";
        assert_eq!(format_uuid(parse_uuid(s).unwrap()), s);
    }
}
