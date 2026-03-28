use std::fs::File;
use std::io::{BufWriter, Write};
use std::time::Duration;

use chrono::{Utc, Duration as ChronoDuration};
use futures::stream::{self, StreamExt};
use reqwest::Client;
use tokio::time::timeout;

const CONCURRENT_REQUESTS: usize = 100;
const REQUEST_TIMEOUT: u64 = 5;

#[derive(Debug)]
struct Playlist {
    name: String,
    url: String,
    entries: Vec<PlaylistEntry>,
    valid_count: usize,
    total_count: usize,
}

#[derive(Debug, Clone)]
struct PlaylistEntry {
    content: String,
    entry_type: EntryType,
    original_index: usize,
    is_valid: bool,
    response_time: Option<Duration>,
    error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
enum EntryType {
    Header,
    Metadata,
    StreamUrl,
    Comment,
    Empty,
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
        .map_err(|e| format!("Không thể đọc file {}: {}", input_file, e))?;
    println!("📥 Đã đọc file {} ({} bytes)", input_file, content.len());

    let entries = parse_playlist_content(&content);
    let original_count = entries.iter().filter(|e| e.entry_type == EntryType::StreamUrl).count();
    println!("📊 Tổng số dòng: {}, trong đó URL streams: {}", entries.len(), original_count);

    let (filtered_entries, filtered_count) = filter_unwanted_entries(entries);
    if filtered_count > 0 {
        println!("🚫 Đã lọc bỏ {} stream (adult/MP4/cinehub)", filtered_count);
    }

    let streams_to_validate: Vec<(usize, String)> = filtered_entries
        .iter()
        .filter(|e| e.entry_type == EntryType::StreamUrl)
        .map(|e| (e.original_index, e.content.clone()))
        .collect();
    let total_to_check = streams_to_validate.len();
    println!("🔬 Số stream cần kiểm tra: {}", total_to_check);

    let client = create_http_client();
    let validation_results = validate_streams_batch(&client, &streams_to_validate).await;

    let validated_entries = update_entries_with_validation(filtered_entries, validation_results);
    let valid_count = validated_entries.iter().filter(|e| e.is_valid).count();

    let mut playlist = Playlist::new("Output Cleaned".to_string(), input_file.to_string());
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
    ║       Local M3U Stream Validator      ║
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
        .default_headers({
            let mut headers = reqwest::header::HeaderMap::new();
            headers.insert(
                reqwest::header::USER_AGENT,
                reqwest::header::HeaderValue::from_static("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            );
            headers.insert(
                reqwest::header::ACCEPT,
                reqwest::header::HeaderValue::from_static("video/*, application/vnd.apple.mpegurl, */*")
            );
            headers.insert(
                reqwest::header::ACCEPT_LANGUAGE,
                reqwest::header::HeaderValue::from_static("en-US,en;q=0.9,vi;q=0.8")
            );
            headers
        })
        .build()
        .expect("Failed to create HTTP client")
}

fn parse_playlist_content(content: &str) -> Vec<PlaylistEntry> {
    let mut entries = Vec::new();
    let mut index = 0;
    for line in content.lines() {
        let line = line.trim();
        let entry_type = classify_line(line);
        let entry = PlaylistEntry {
            content: line.to_string(),
            entry_type,
            original_index: index,
            is_valid: false,
            response_time: None,
            error: None,
        };
        entries.push(entry);
        index += 1;
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
    } else if line.starts_with("#EXT") || line.starts_with("#PLAYLIST") || line.starts_with("#EXTVLCOPT") {
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
                entry.content.to_uppercase().contains("ADULT")
                    || entry.content.contains("group-title=\"ADULT")
            }
            EntryType::StreamUrl => {
                entry.content.contains("cinehub24.com")
                    || entry.content.to_lowercase().ends_with(".mp4")
                    || entry.content.contains(".mp4?")
            }
            _ => false,
        };

        if should_filter {
            if entry.entry_type == EntryType::Metadata {
                skip_next_url = true;
                filtered_count += 1;
            } else if entry.entry_type == EntryType::StreamUrl {
                filtered_count += 1;
            }
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
    streams: &[(usize, String)],
) -> Vec<(usize, bool, Option<Duration>, Option<String>)> {
    let total_streams = streams.len();

    let validation_results: Vec<(usize, bool, Option<Duration>, Option<String>)> = stream::iter(streams.iter().enumerate())
        .map(|(batch_index, (original_index, url))| {
            let client = client.clone();
            let url = url.clone();
            let original_index = *original_index;

            async move {
                let check_start = std::time::Instant::now();
                let validation_result = if url.starts_with("udp://") {
                    ValidationResult { is_valid: true, error: None }
                } else {
                    perform_deep_validation(&client, &url).await
                };
                let response_time = check_start.elapsed();

                let progress = format!("[{}/{}]", batch_index + 1, total_streams);
                let channel_name = extract_channel_name_from_url(&url)
                    .unwrap_or_else(|| shorten_url(&url));

                if validation_result.is_valid {
                    let speed = format!("{}ms", response_time.as_millis());
                    println!("   ✅ {} {} - ({})", progress, channel_name, speed);
                } else {
                    let error_msg = validation_result.error.as_deref().unwrap_or("Unknown error");
                    println!("   ❌ {} {} - {}", progress, channel_name, error_msg);
                }

                (
                    original_index,
                    validation_result.is_valid,
                    Some(response_time),
                    validation_result.error,
                )
            }
        })
        .buffer_unordered(CONCURRENT_REQUESTS)
        .collect()
        .await;

    validation_results
}

fn update_entries_with_validation(
    entries: Vec<PlaylistEntry>,
    validation_results: Vec<(usize, bool, Option<Duration>, Option<String>)>,
) -> Vec<PlaylistEntry> {
    let mut result_map = std::collections::HashMap::new();
    for (index, is_valid, response_time, error) in validation_results {
        result_map.insert(index, (is_valid, response_time, error));
    }

    let mut updated_entries = entries;
    for entry in &mut updated_entries {
        if entry.entry_type == EntryType::StreamUrl {
            if let Some((is_valid, response_time, error)) = result_map.get(&entry.original_index) {
                entry.is_valid = *is_valid;
                entry.response_time = *response_time;
                entry.error = error.clone();
            }
        }
    }

    updated_entries
}

async fn perform_deep_validation(client: &Client, url: &str) -> ValidationResult {
    let request_builder = client.get(url).header("Range", "bytes=0-1024");
    match timeout(Duration::from_secs(REQUEST_TIMEOUT), request_builder.send()).await {
        Ok(Ok(resp)) => {
            let status = resp.status();
            let body_text = match timeout(Duration::from_secs(5), resp.text()).await {
                Ok(Ok(text)) => text,
                _ => String::new(),
            };
            if let Some(error_msg) = extract_error_from_body(&body_text) {
                return ValidationResult { is_valid: false, error: Some(error_msg) };
            }
            if status.is_success() || status.as_u16() == 206 {
                if url.contains(".m3u8") {
                    if body_text.contains("#EXTM3U") || body_text.contains("#EXTINF") {
                        ValidationResult { is_valid: true, error: None }
                    } else {
                        ValidationResult { is_valid: false, error: Some("Invalid HLS playlist".to_string()) }
                    }
                } else {
                    if body_text.contains("<html") || body_text.contains("<body") {
                        if let Some(html_error) = extract_html_error(&body_text) {
                            ValidationResult { is_valid: false, error: Some(html_error) }
                        } else {
                            ValidationResult { is_valid: false, error: Some("Server returned HTML instead of stream".to_string()) }
                        }
                    } else {
                        ValidationResult { is_valid: true, error: None }
                    }
                }
            } else {
                let error_msg = if !body_text.is_empty() {
                    extract_error_from_body(&body_text).unwrap_or_else(|| format!("HTTP {}", status))
                } else {
                    format!("HTTP {}", status)
                };
                ValidationResult { is_valid: false, error: Some(error_msg) }
            }
        }
        Ok(Err(e)) => ValidationResult { is_valid: false, error: Some(format!("Request error: {}", e)) },
        Err(_) => ValidationResult { is_valid: false, error: Some("Timeout".to_string()) },
    }
}

fn extract_error_from_body(body: &str) -> Option<String> {
    let lower_body = body.to_lowercase();
    let error_keywords = [
        "access denied", "geo-blocked", "geo blocked", "unauthorized",
        "forbidden", "not authorized", "401", "403", "this page isn’t working",
        "http error 401", "error", "invalid request", "not found"
    ];
    if error_keywords.iter().any(|kw| lower_body.contains(kw)) {
        let snippet = body
            .lines()
            .find(|line| {
                let l = line.to_lowercase();
                error_keywords.iter().any(|kw| l.contains(kw))
            })
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|| "Access denied/Geo-blocked".to_string());
        Some(snippet)
    } else {
        None
    }
}

fn extract_html_error(body: &str) -> Option<String> {
    let lower = body.to_lowercase();
    if lower.contains("401") || lower.contains("unauthorized") || lower.contains("access denied") {
        let snippet = body
            .lines()
            .find(|line| {
                let l = line.to_lowercase();
                l.contains("401") || l.contains("unauthorized") || l.contains("access denied")
            })
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|| "HTTP 401 Unauthorized".to_string());
        Some(snippet)
    } else {
        None
    }
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
    writeln!(writer, "#GENERATED-BY: CXT Cleaner v2.0")?;
    writeln!(writer, "#VALIDATION-DATE: {}", date_str)?;
    writeln!(writer, "#TOTAL-STREAMS-CHECKED: {}", total_checked)?;
    writeln!(writer, "#TOTAL-VALID-STREAMS: {}", valid_count)?;
    if total_checked > 0 {
        writeln!(
            writer,
            "#SUCCESS-RATE: {:.1}%",
            (valid_count as f32 / total_checked as f32) * 100.0
        )?;
    } else {
        writeln!(writer, "#SUCCESS-RATE: N/A")?;
    }
    writeln!(writer, "#FILTERS-APPLIED: ADULT, .mp4, cinehub24.com")?;
    writeln!(writer, "#ORDER-PRESERVATION: Original channel order maintained")?;
    writeln!(writer, "########################################")?;

    let mut last_metadata: Option<&PlaylistEntry> = None;
    for entry in &playlist.entries {
        match entry.entry_type {
            EntryType::Header => {}
            EntryType::Metadata => {
                last_metadata = Some(entry);
            }
            EntryType::StreamUrl => {
                if entry.is_valid {
                    if let Some(metadata) = last_metadata.take() {
                        writeln!(writer, "{}", metadata.content)?;
                    }
                    writeln!(writer, "{}", entry.content)?;
                } else {
                    last_metadata = None;
                }
            }
            EntryType::Comment | EntryType::Empty => {
                if last_metadata.is_none() {
                    writeln!(writer, "{}", entry.content)?;
                }
            }
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
        println!(
            "   Success Rate: {:.1}%",
            (valid_count as f32 / total_checked as f32) * 100.0
        );
    } else {
        println!("   Success Rate: N/A");
    }
    println!("   Order Preservation: ✅ Original order maintained");
    println!("---------------------------------------------");
    println!("🚫 Active Filters:");
    println!("   • Adult content (group-title=\"ADULT LIVE\")");
    println!("   • cinehub24.com domains");
    println!("   • .mp4 format streams");
    println!("=============================================");
}

fn extract_channel_name_from_url(url: &str) -> Option<String> {
    if let Some(domain_start) = url.find("://") {
        let domain_end = url[domain_start + 3..].find('/').unwrap_or(url.len() - domain_start - 3);
        let domain = &url[domain_start + 3..domain_start + 3 + domain_end];
        Some(domain.to_string())
    } else {
        None
    }
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
