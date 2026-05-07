import 'package:flutter/material.dart';

class AnomalyEvent {
  final String eventType; // meal_skip | med_skip | wandering | silence
  final String severity;  // low | medium | high
  final String description;
  final DateTime timestamp;
  final String? id;
  bool resolved;

  AnomalyEvent({
    required this.eventType,
    required this.severity,
    required this.description,
    required this.timestamp,
    this.id,
    this.resolved = false,
  });

  factory AnomalyEvent.fromJson(Map<String, dynamic> json) {
    return AnomalyEvent(
      eventType: json['event_type']?.toString() ?? 'unknown',
      severity: json['severity']?.toString() ?? 'low',
      description: json['description']?.toString() ?? '',
      timestamp: json['timestamp'] != null
          ? DateTime.tryParse(json['timestamp'].toString()) ?? DateTime.now()
          : DateTime.now(),
      id: json['id']?.toString(),
      resolved: json['resolved'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'event_type': eventType,
      'severity': severity,
      'description': description,
      'timestamp': timestamp.toIso8601String(),
      'id': id,
      'resolved': resolved,
    };
  }

  Color get severityColor {
    switch (severity) {
      case 'high':
        return const Color(0xFFD32F2F);
      case 'medium':
        return const Color(0xFFF57C00);
      case 'low':
      default:
        return const Color(0xFFF9A825);
    }
  }

  IconData get eventIcon {
    switch (eventType) {
      case 'meal_skip':
        return Icons.no_meals;
      case 'med_skip':
        return Icons.medication_liquid;
      case 'wandering':
        return Icons.location_off;
      case 'silence':
        return Icons.volume_off;
      default:
        return Icons.warning_amber;
    }
  }

  String get eventTypeLabel {
    switch (eventType) {
      case 'meal_skip':
        return 'Meal Skipped';
      case 'med_skip':
        return 'Medication Missed';
      case 'wandering':
        return 'Wandering Detected';
      case 'silence':
        return 'Unusual Silence';
      default:
        return eventType.replaceAll('_', ' ').toUpperCase();
    }
  }

  String get severityLabel {
    switch (severity) {
      case 'high':
        return 'HIGH';
      case 'medium':
        return 'MEDIUM';
      case 'low':
      default:
        return 'LOW';
    }
  }

  String get timeAgo {
    final now = DateTime.now();
    final diff = now.difference(timestamp);
    if (diff.inMinutes < 1) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }
}
