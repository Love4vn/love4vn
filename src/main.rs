use std::fs::File;
use std::io::{BufWriter, Write};
use std::time::Duration;

use chrono::{Utc, Duration as ChronoDuration};
use futures::stream::{self, StreamExt};
use reqwest::Client;
use tokio::time::timeout;

const CONCURRENT_REQUESTS: usize = 100;
const REQUEST_TIMEOUT: u64 = 5;

#[derive(Debug, Clone)]
struct PlaylistEntry {
    content: String,
    entry_type: EntryType,
    original_index: usize,
    is_valid: bool,
    response_time: Option<Duration>,
    error: Option<String>,
    // Stalker portal options
    vlc_user_agent: Option<String>,
    vlc_cookie: Option<String>,
    vlc_headers: Vec<(String, String)>,
}

#[derive(Debug, Clone, PartialEq)]
enum EntryType {
    Header,
    Metadata,
    StreamUrl,
    Comment,
    VlcOpt,
    Empty,
}

impl PlaylistEntry {
    fn new(content: String, entry_type: EntryType, index: usize) -> Self {
        Self {
            content,
            entry_type,
            original_index: index,
            is_valid: false,
            response_time: None,
            error: None,
            vlc_user_agent: None,
            vlc_cookie: None,
            vlc_headers: Vec::new(),
        }
    }
}

#[derive(Debug)]
struct Playlist {
    name: String,
    url: String,
    entries: Vec<PlaylistEntry>,
    valid_count: usize,
    total_count: usize,
}

impl Playlist {
    fn new(name: String, url: String) -> Self {
        Self {
            name,
            url,
            entries: Vec::new(),
            valid_count: 0,
            total_count: 0,
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    print_banner();
    println!("📂 Cleaning output.m3u -> output_clean.m3u");
    println!("=============================================");
    println!("⚡ Concurrency: {} streams", CONCURRENT_REQUESTS);
    println!("⏱️  Timeout: {} seconds", REQUEST_TIMEOUT);
    println!("🔒 Preserving original channel order");
    println!("---------------------------------------------");

    let input_file = "output.m3u";
    let output_file = "output_clean.m3u";

    let content = std::fs::read_to_string(input_file)
        .map_err(|e| format!("Cannot read {}: {}", input_file, e))?;
    println!("📥 Loaded {} ({} bytes)", input_file, content.len());

    let entries = parse_playlist_content(&content);
    let original_count = entries.iter().filter(|e| e.entry_type == EntryType::StreamUrl).count();
    println!("📊 Total lines: {}, Stream URLs: {}", entries.len(), original_count);

    let (filtered_entries, filtered_count) = filter_unwanted_entries(entries);
    if filtered_count > 0 {
        println!("🚫 Filtered {} streams (adult/MP4/cinehub)", filtered_count);
    }

    // Prepare list of (index, url, options) for validation
    let streams_to_validate: Vec<(usize, String, Option<String>, Option<String>, Vec<(String, String)>)> = filtered_entries
        .iter()
        .filter(|e| e.entry_type == EntryType::StreamUrl)
        .map(|e| (e.original_index, e.content.clone(), e.vlc_user_agent.clone(), e.vlc_cookie.clone(), e.vlc_headers.clone()))
        .collect();
    let total_to_check = streams_to_validate.len();
    println!("🔬 Validating {} streams...", total_to_check);

    let client = create_http_client();
    let validation_results = validate_streams_batch(&client, &streams_to_validate).await;

    let validated_entries = update_entries_with_validation(filtered_entries, validation_results);
    let valid_count = validated_entries.iter().filter(|e| e.is_valid).count();

    let mut playlist = Playlist::new("Cleaned Playlist".to_string(), input_file.to_string());
    playlist.entries = validated_entries;
    playlist.valid_count = valid_count;
    playlist.total_count = original_count;

    write_cleaned_playlist(output_file, &playlist, valid_count, total_to_check)?;

    print_summary(&playlist, valid_count, total_to_check);

    Ok(())
}

fn print_banner() {
    println!(
        r#"
    ╔═══════════════════════════════════════╗
    ║              🧹 CXT Cleaner           ║
    ║       Stalker Portal Ready            ║
    ║      Order-Preserving System          ║
    ╚═══════════════════════════════════════╝
    "#
    );
}

fn create_http_client() -> Client {
    Client::builder()
        .timeout(Duration::from_secs(REQUEST_TIMEOUT))
        .tcp_keepalive(Duration::from_secs(10))
        .pool_max_idle_per_host(50)
        .user_agent("CXT-Cleaner/2.0")
        .build()
        .expect("Failed to create HTTP client")
}

fn parse_playlist_content(content: &str) -> Vec<PlaylistEntry> {
    let mut entries = Vec::new();
    let mut index = 0;
    let mut pending_vlc_ua: Option<String> = None;
    let mut pending_vlc_cookie: Option<String> = None;
    let mut pending_vlc_headers: Vec<(String, String)> = Vec::new();

    for line in content.lines() {
        let line = line.trim();
        let entry_type = classify_line(line);
        let mut entry = PlaylistEntry::new(line.to_string(), entry_type.clone(), index);
        index += 1;

        match entry_type {
            EntryType::VlcOpt => {
                // Parse #EXTVLCOPT: key=value or http-header=Key: Value
                if let Some(rest) = line.strip_prefix("#EXTVLCOPT:") {
                    if rest.starts_with("http-user-agent=") {
                        let ua = rest["http-user-agent=".len()..].to_string();
                        pending_vlc_ua = Some(ua);
                    } else if rest.starts_with("http-cookie=") {
                        let cookie = rest["http-cookie=".len()..].to_string();
                        pending_vlc_cookie = Some(cookie);
                    } else if rest.starts_with("http-header=") {
                        let header_part = &rest["http-header=".len()..];
                        if let Some(colon_pos) = header_part.find(':') {
                            let key = header_part[..colon_pos].trim().to_string();
                            let value = header_part[colon_pos+1..].trim().to_string();
                            pending_vlc_headers.push((key, value));
                        }
                    }
                }
                entries.push(entry);
            }
            EntryType::StreamUrl => {
                // Attach pending VLC options to this URL
                entry.vlc_user_agent = pending_vlc_ua.take();
                entry.vlc_cookie = pending_vlc_cookie.take();
                entry.vlc_headers = pending_vlc_headers.drain(..).collect();
                entries.push(entry);
            }
            _ => {
                // For non-VLC and non-URL, clear pending options? Usually options only apply to next URL.
                // But we keep them until a URL appears. However, if we encounter a new #EXTINF or comment, reset? 
                // Standard M3U: #EXTVLCOPT lines before a URL belong to that URL.
                // After a URL, options should be cleared. For safety, we clear pending options when we see a new #EXTINF or header.
                if entry_type == EntryType::Metadata || entry_type == EntryType::Header {
                    pending_vlc_ua = None;
                    pending_vlc_cookie = None;
                    pending_vlc_headers.clear();
                }
                entries.push(entry);
            }
        }
    }
    entries
}

fn classify_line(line: &str) -> EntryType {
    if line.is_empty() {
        EntryType::Empty
    } else if line == "#EXTM3U" {
        EntryType::Header
    } else if line.starts_with("#EXTINF") {
        EntryType::Metadata
    } else if line.starts_with("#EXTVLCOPT") {
        EntryType::VlcOpt
    } else if line.starts_with("#EXT") || line.starts_with("#PLAYLIST") {
        EntryType::Comment
    } else if line.starts_with("http") || line.starts_with("udp://") {
        EntryType::StreamUrl
    } else {
        EntryType::Comment
    }
}

fn filter_unwanted_entries(entries: Vec<PlaylistEntry>) -> (Vec<PlaylistEntry>, usize) {
    let mut filtered = Vec::new();
    let mut skip_next_url = false;
    let mut filtered_count = 0;
    let mut i = 0;

    while i < entries.len() {
        let entry = &entries[i];
        let should_filter = match entry.entry_type {
            EntryType::Metadata => {
                entry.content.to_uppercase().contains("ADULT") ||
                entry.content.contains("group-title=\"ADULT")
            }
            EntryType::StreamUrl => {
                entry.content.contains("cinehub24.com") ||
                entry.content.to_lowercase().ends_with(".mp4") ||
                entry.content.contains(".mp4?")
            }
            _ => false,
        };

        if should_filter {
            if entry.entry_type == EntryType::Metadata { skip_next_url = true; }
            filtered_count += 1;
            i += 1;
        } else if skip_next_url && entry.entry_type == EntryType::StreamUrl {
            filtered_count += 1;
            skip_next_url = false;
            i += 1;
        } else {
            skip_next_url = false;
            filtered.push(entries[i].clone());
            i += 1;
        }
    }
    (filtered, filtered_count)
}

async fn validate_streams_batch(
    client: &Client,
    streams: &[(usize, String, Option<String>, Option<String>, Vec<(String, String)>)],
) -> Vec<(usize, bool, Option<Duration>, Option<String>)> {
    let total = streams.len();
    let results = stream::iter(streams.iter().enumerate())
        .map(|(idx, (orig_idx, url, ua, cookie, headers))| {
            let client = client.clone();
            let url = url.clone();
            let ua = ua.clone();
            let cookie = cookie.clone();
            let headers = headers.clone();
            let orig_idx = *orig_idx;
            async move {
                let start = std::time::Instant::now();
                let result = if url.starts_with("udp://") {
                    ValidationResult { is_valid: true, error: None }
                } else {
                    validate_with_options(&client, &url, ua, cookie, &headers).await
                };
                let elapsed = start.elapsed();
                let progress = format!("[{}/{}]", idx+1, total);
                let short = extract_channel_name_from_url(&url).unwrap_or_else(|| shorten_url(&url));
                if result.is_valid {
                    println!("   ✅ {} {} - ({}ms)", progress, short, elapsed.as_millis());
                } else {
                    let err = result.error.as_deref().unwrap_or("Unknown");
                    println!("   ❌ {} {} - {}", progress, short, err);
                }
                (orig_idx, result.is_valid, Some(elapsed), result.error)
            }
        })
        .buffer_unordered(CONCURRENT_REQUESTS)
        .collect()
        .await;
    results
}

async fn validate_with_options(
    client: &Client,
    url: &str,
    user_agent: Option<String>,
    cookie: Option<String>,
    extra_headers: &[(String, String)],
) -> ValidationResult {
    // HEAD request with custom headers
    let mut head_builder = client.head(url);
    if let Some(ua) = &user_agent {
        head_builder = head_builder.header(reqwest::header::USER_AGENT, ua);
    }
    if let Some(c) = &cookie {
        head_builder = head_builder.header(reqwest::header::COOKIE, c);
    }
    for (k, v) in extra_headers {
        head_builder = head_builder.header(k, v);
    }

    match timeout(Duration::from_secs(REQUEST_TIMEOUT), head_builder.send()).await {
        Ok(Ok(resp)) => {
            let status = resp.status();
            // Read body to detect hidden errors
            let body_text = timeout(Duration::from_secs(3), resp.text()).await.ok().unwrap_or(Ok(String::new())).unwrap_or_default();
            if let Some(err_msg) = extract_error_from_body(&body_text) {
                return ValidationResult { is_valid: false, error: Some(err_msg) };
            }
            if status.is_success() {
                return ValidationResult { is_valid: true, error: None };
            } else {
                return ValidationResult { is_valid: false, error: Some(format!("HTTP {}", status)) };
            }
        }
        Ok(Err(e)) => return ValidationResult { is_valid: false, error: Some(format!("Request error: {}", e)) },
        Err(_) => {} // timeout, fallback to GET
    }

    // GET with Range
    let mut get_builder = client.get(url).header("Range", "bytes=0-1024");
    if let Some(ua) = user_agent {
        get_builder = get_builder.header(reqwest::header::USER_AGENT, ua);
    }
    if let Some(c) = cookie {
        get_builder = get_builder.header(reqwest::header::COOKIE, c);
    }
    for (k, v) in extra_headers {
        get_builder = get_builder.header(k, v);
    }

    match timeout(Duration::from_secs(REQUEST_TIMEOUT), get_builder.send()).await {
        Ok(Ok(resp)) => {
            let status = resp.status();
            let body_text = timeout(Duration::from_secs(3), resp.text()).await.ok().unwrap_or(Ok(String::new())).unwrap_or_default();
            if let Some(err_msg) = extract_error_from_body(&body_text) {
                return ValidationResult { is_valid: false, error: Some(err_msg) };
            }
            if status.is_success() || status.as_u16() == 206 {
                if url.contains(".m3u8") {
                    if body_text.contains("#EXTM3U") || body_text.contains("#EXTINF") {
                        return ValidationResult { is_valid: true, error: None };
                    } else {
                        return ValidationResult { is_valid: false, error: Some("Invalid HLS playlist".to_string()) };
                    }
                }
                return ValidationResult { is_valid: true, error: None };
            } else {
                let err = if !body_text.is_empty() {
                    extract_error_from_body(&body_text).unwrap_or_else(|| format!("HTTP {}", status))
                } else {
                    format!("HTTP {}", status)
                };
                return ValidationResult { is_valid: false, error: Some(err) };
            }
        }
        Ok(Err(e)) => ValidationResult { is_valid: false, error: Some(format!("Request error: {}", e)) },
        Err(_) => ValidationResult { is_valid: false, error: Some("Timeout".to_string()) },
    }
}

fn extract_error_from_body(body: &str) -> Option<String> {
    let lower = body.to_lowercase();
    if lower.contains("access denied")
        || lower.contains("geo-blocked")
        || lower.contains("unauthorized")
        || lower.contains("forbidden")
        || lower.contains("401")
        || lower.contains("403")
        || lower.contains("incorrect key")
        || lower.contains("this page isn’t working")
        || lower.contains("http error")
    {
        let snippet = body.lines()
            .find(|l| {
                let lc = l.to_lowercase();
                lc.contains("access") || lc.contains("denied") || lc.contains("unauthorized")
                || lc.contains("incorrect key") || lc.contains("401") || lc.contains("this page")
            })
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|| "Access denied/Invalid key".to_string());
        Some(snippet)
    } else {
        None
    }
}

fn update_entries_with_validation(
    mut entries: Vec<PlaylistEntry>,
    results: Vec<(usize, bool, Option<Duration>, Option<String>)>,
) -> Vec<PlaylistEntry> {
    let mut map = std::collections::HashMap::new();
    for (idx, valid, dur, err) in results {
        map.insert(idx, (valid, dur, err));
    }
    for entry in &mut entries {
        if entry.entry_type == EntryType::StreamUrl {
            if let Some((valid, dur, err)) = map.get(&entry.original_index) {
                entry.is_valid = *valid;
                entry.response_time = *dur;
                entry.error = err.clone();
            }
        }
    }
    entries
}

fn write_cleaned_playlist(
    output_file: &str,
    playlist: &Playlist,
    valid_count: usize,
    total_checked: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    let file = File::create(output_file)?;
    let mut writer = BufWriter::new(file);

    let now_vn = Utc::now() + ChronoDuration::hours(7);
    let date_str = now_vn.format("%Y-%m-%d %H:%M:%S").to_string();

    writeln!(writer, "#EXTM3U")?;
    writeln!(writer, "#PLAYLIST:{} - Cleaned", playlist.name)?;
    writeln!(writer, "#GENERATED-BY: CXT Cleaner v2.0 (Stalker Ready)")?;
    writeln!(writer, "#VALIDATION-DATE: {}", date_str)?;
    writeln!(writer, "#TOTAL-STREAMS-CHECKED: {}", total_checked)?;
    writeln!(writer, "#TOTAL-VALID-STREAMS: {}", valid_count)?;
    if total_checked > 0 {
        writeln!(writer, "#SUCCESS-RATE: {:.1}%", (valid_count as f32 / total_checked as f32) * 100.0)?;
    } else {
        writeln!(writer, "#SUCCESS-RATE: N/A")?;
    }
    writeln!(writer, "#FILTERS-APPLIED: ADULT, .mp4, cinehub24.com")?;
    writeln!(writer, "#ORDER-PRESERVATION: Original order maintained")?;
    writeln!(writer, "########################################")?;

    let mut last_metadata: Option<&PlaylistEntry> = None;
    for entry in &playlist.entries {
        match entry.entry_type {
            EntryType::Header => {}
            EntryType::Metadata => {
                last_metadata = Some(entry);
            }
            EntryType::StreamUrl if entry.is_valid => {
                if let Some(meta) = last_metadata.take() {
                    writeln!(writer, "{}", meta.content)?;
                }
                // Write any VLC options that were attached to this URL
                if let Some(ua) = &entry.vlc_user_agent {
                    writeln!(writer, "#EXTVLCOPT:http-user-agent={}", ua)?;
                }
                if let Some(cookie) = &entry.vlc_cookie {
                    writeln!(writer, "#EXTVLCOPT:http-cookie={}", cookie)?;
                }
                for (k, v) in &entry.vlc_headers {
                    writeln!(writer, "#EXTVLCOPT:http-header={}: {}", k, v)?;
                }
                writeln!(writer, "{}", entry.content)?;
            }
            EntryType::Comment | EntryType::Empty | EntryType::VlcOpt => {
                // Preserve comments and VLC options only if they are not part of filtered stream
                // But we already handle VLC options by attaching to URL. To avoid duplication,
                // we skip writing raw VLC options because they are rewritten above.
                if entry.entry_type != EntryType::VlcOpt {
                    writeln!(writer, "{}", entry.content)?;
                }
            }
            _ => {}
        }
    }

    writeln!(writer, "########################################")?;
    writeln!(writer, "#END-OF-PLAYLIST")?;
    writeln!(writer, "#CXT - Quality Streams Only - Order Preserved")?;
    writer.flush()?;
    println!("\n💾 Playlist saved: {}", output_file);
    Ok(())
}

fn print_summary(playlist: &Playlist, valid_count: usize, total_checked: usize) {
    println!("\n=============================================");
    println!("🧹 CXT - CLEANING COMPLETE");
    println!("=============================================");
    println!("📊 SUMMARY:");
    println!("   Input file: {}", playlist.url);
    println!("   Original streams: {}", playlist.total_count);
    println!("   Checked: {}", total_checked);
    println!("   Valid: {}", valid_count);
    if total_checked > 0 {
        println!("   Success Rate: {:.1}%", (valid_count as f32 / total_checked as f32) * 100.0);
    }
    println!("   Order Preservation: ✅");
    println!("   Stalker Portal Support: ✅ (User-Agent, Cookie, Headers)");
    println!("---------------------------------------------");
    println!("🚫 Active Filters:");
    println!("   • Adult content (group-title=\"ADULT LIVE\")");
    println!("   • cinehub24.com domains");
    println!("   • .mp4 format streams");
    println!("=============================================");
}

fn extract_channel_name_from_url(url: &str) -> Option<String> {
    url.find("://").and_then(|start| {
        let domain_start = start + 3;
        let domain_end = url[domain_start..].find('/').unwrap_or(url.len() - domain_start);
        Some(url[domain_start..domain_start+domain_end].to_string())
    })
}

fn shorten_url(url: &str) -> String {
    if url.len() > 40 {
        format!("{}...", &url[..40])
    } else {
        url.to_string()
    }
}

#[derive(Debug)]
struct ValidationResult {
    is_valid: bool,
    error: Option<String>,
        }
