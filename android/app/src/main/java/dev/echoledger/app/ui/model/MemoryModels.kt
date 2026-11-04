package dev.echoledger.app.ui.model

data class TranscriptSegment(
    val id: String,
    val speaker: String,
    val time: String,
    val text: String
)

data class SubFragmentItem(
    val id: String,
    val title: String,
    val duration: String,
    val startTime: String = "",
    val endTime: String = "",
    val detailDuration: String = "",
    val fullDateLabel: String = "",
    val listDescription: String = "",
    val overviewSummary: String = "",
    val unrecognizedVoiceCount: Int = 1,
    val transcripts: List<TranscriptSegment> = emptyList()
)

data class MemoryEvent(
    val id: String,
    val dayNumber: Int, // 2 = Nov 2, 3 = Nov 3, 30 = Oct 30
    val dateIso: String = "", // e.g. "2026-11-04"
    val dayGroupLabel: String, // "昨天", "前天", "10月30日 周五"
    val datePrefixForPerson: String, // "昨天", "2026/11/02", "2026/10/30"
    val fullDateLabel: String,
    val startTime: String,
    val endTime: String,
    val durationLabel: String,
    val categoryWatermark: String, // "LEARNING", "MEETING", "WORK", "MEAL", "SOCIAL", ""
    val title: String,
    val speakers: List<String>,
    val unrecognizedVoiceCount: Int = 0,
    val summaryOverview: String,
    val subFragmentsStartTime: String = "",
    val subFragmentsEndTime: String = "",
    val subFragments: List<SubFragmentItem> = emptyList(),
    val transcripts: List<TranscriptSegment> = emptyList(),
    val detailedNoteMarkdown: String = ""
)

data class UnrecognizedVoiceClip(
    val id: String,
    val quote: String,
    val timestamp: String,
    val occurrences: Int,
    val sampleCount: Int
)

data class PersonVoiceProfile(
    val name: String,
    val dialogueCount: Int
)

data class AgentItem(
    val id: String,
    val name: String,
    val iconType: String
)

data class CalendarDayItem(
    val weekDayLabel: String,
    val dayNumber: Int,
    val dotCount: Int
)

object SampleMemoryData {
    val calendarDaysWeek1 = emptyList<CalendarDayItem>()
    val initialPeople = emptyList<PersonVoiceProfile>()
    val initialUnrecognizedClips = emptyList<UnrecognizedVoiceClip>()

    val agentList = listOf(
        AgentItem("claude", "Claude", "spark"),
        AgentItem("codex", "OpenAI Codex", "vortex"),
        AgentItem("hermes", "Hermes", "greek"),
        AgentItem("cursor", "Cursor", "cube"),
        AgentItem("antigravity", "Antigravity", "triangle"),
        AgentItem("openclaw", "OpenClaw", "crab"),
        AgentItem("grok", "Grok", "slash"),
        AgentItem("workbuddy", "WorkBuddy", "buddy"),
        AgentItem("deepseek", "DeepSeek", "whale"),
        AgentItem("more", "更多", "dots")
    )

    val events = emptyList<MemoryEvent>()
}

fun SubFragmentItem.toMemoryEvent(dayNumber: Int = 2): MemoryEvent {
    return MemoryEvent(
        id = id,
        dayNumber = dayNumber,
        dayGroupLabel = "前天",
        datePrefixForPerson = "2026/11/02",
        fullDateLabel = fullDateLabel.ifEmpty { "2026年11月2日 周一 · $startTime 至 $endTime · $detailDuration" },
        startTime = startTime,
        endTime = endTime,
        durationLabel = detailDuration.ifEmpty { duration },
        categoryWatermark = "",
        title = title,
        speakers = emptyList(),
        unrecognizedVoiceCount = unrecognizedVoiceCount,
        summaryOverview = overviewSummary.ifEmpty { listDescription },
        transcripts = transcripts
    )
}

fun jsonToMemoryEvent(json: org.json.JSONObject): MemoryEvent {
    val startMs = json.optLong("start_ms", System.currentTimeMillis())
    val endMs = json.optLong("end_ms", startMs + 60000)
    val zone = java.time.ZoneId.systemDefault()
    val startDateTime = java.time.Instant.ofEpochMilli(startMs).atZone(zone)
    val endDateTime = java.time.Instant.ofEpochMilli(endMs).atZone(zone)
    val dayNumber = startDateTime.dayOfMonth
    val timeFormatter = java.time.format.DateTimeFormatter.ofPattern("HH:mm")
    val startTime = startDateTime.format(timeFormatter)
    val endTime = endDateTime.format(timeFormatter)

    val durationMinutes = ((endMs - startMs) / 60000).coerceAtLeast(1)
    val durationLabel = if (durationMinutes >= 60) {
        val h = durationMinutes / 60
        val m = durationMinutes % 60
        if (m > 0) "${h}h ${m}m" else "${h}h"
    } else {
        "${durationMinutes}m"
    }

    val today = java.time.LocalDate.now(zone)
    val eventDate = startDateTime.toLocalDate()
    val daysDiff = java.time.temporal.ChronoUnit.DAYS.between(eventDate, today)
    val dayGroupLabel = when (daysDiff) {
        0L -> "今天"
        1L -> "昨天"
        2L -> "前天"
        else -> startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("MM月dd日 E"))
    }
    val datePrefix = when (daysDiff) {
        0L -> "今天"
        1L -> "昨天"
        2L -> "前天"
        else -> startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("yyyy/MM/dd"))
    }
    val fullDateLabel = "${startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("yyyy年MM月dd日 E"))} · $startTime 至 $endTime · $durationLabel"

    val speakersList = mutableListOf<String>()
    val speakerNamesArr = json.optJSONArray("speaker_names")
    if (speakerNamesArr != null) {
        for (i in 0 until speakerNamesArr.length()) {
            val name = speakerNamesArr.optString(i)
            if (name.isNotBlank()) speakersList.add(name)
        }
    }

    val typeId = json.optString("type_id", "").uppercase()
    val watermark = when {
        typeId.contains("MEET") -> "MEETING"
        typeId.contains("LEARN") -> "LEARNING"
        typeId.contains("WORK") -> "WORK"
        typeId.contains("MEAL") -> "MEAL"
        typeId.contains("SOCIAL") -> "SOCIAL"
        else -> typeId.take(8)
    }

    val segmentsList = mutableListOf<TranscriptSegment>()
    val segArr = json.optJSONArray("segments")
    if (segArr != null) {
        for (i in 0 until segArr.length()) {
            val seg = segArr.optJSONObject(i) ?: continue
            val segStartMs = seg.optLong("start_ms", startMs)
            val segTime = java.time.Instant.ofEpochMilli(segStartMs).atZone(zone).format(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss"))
            val rawSpk = if (seg.isNull("speaker_name")) "" else seg.optString("speaker_name")
            val segSpeaker = if (rawSpk.isBlank() || rawSpk == "null") {
                val local = if (seg.isNull("local_speaker")) "" else seg.optString("local_speaker")
                if (local.isNotBlank() && local != "null") "Speaker $local" else "待确认发言人"
            } else rawSpk
            segmentsList.add(
                TranscriptSegment(
                    id = seg.optString("id", "seg_$i"),
                    speaker = segSpeaker,
                    time = segTime,
                    text = seg.optString("text")
                )
            )
        }
    }

    if (speakersList.isEmpty() && segmentsList.isNotEmpty()) {
        val distinctSpeakers = segmentsList.map { it.speaker }
            .filter { it.isNotBlank() && it != "待确认发言人" && it != "null" }
            .distinct()
        speakersList.addAll(distinctSpeakers)
    }

    val summary = json.optString("summary").ifBlank {
        json.optString("overview").ifBlank {
            json.optString("title")
        }
    }
    val detailedNote = json.optString("detailed_notes").ifBlank {
        json.optString("note")
    }

    return MemoryEvent(
        id = json.optString("id"),
        dayNumber = dayNumber,
        dateIso = eventDate.toString(),
        dayGroupLabel = dayGroupLabel,
        datePrefixForPerson = datePrefix,
        fullDateLabel = fullDateLabel,
        startTime = startTime,
        endTime = endTime,
        durationLabel = durationLabel,
        categoryWatermark = watermark,
        title = json.optString("title", "未命名记录"),
        speakers = speakersList,
        unrecognizedVoiceCount = json.optInt("unassigned_labels", 0),
        summaryOverview = summary,
        transcripts = segmentsList,
        detailedNoteMarkdown = detailedNote
    )
}

fun jsonToPersonVoiceProfile(json: org.json.JSONObject): PersonVoiceProfile {
    return PersonVoiceProfile(
        name = json.optString("name", "未命名"),
        dialogueCount = json.optInt("voiceprint_count", json.optInt("dialogue_count", 1)).coerceAtLeast(1)
    )
}

