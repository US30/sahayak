import 'package:flutter/material.dart';

class MemoryChunk {
  final String id;
  final String userId;
  final DateTime timestamp;
  final String text;
  final List<String> people;
  final Map<String, double>? location;
  final List<String> tags;
  final String memoryType;

  const MemoryChunk({
    required this.id,
    required this.userId,
    required this.timestamp,
    required this.text,
    required this.people,
    this.location,
    required this.tags,
    required this.memoryType,
  });

  factory MemoryChunk.fromJson(Map<String, dynamic> json) {
    Map<String, double>? locationMap;
    if (json['location'] != null) {
      final raw = json['location'] as Map<String, dynamic>;
      locationMap = raw.map((k, v) => MapEntry(k, (v as num).toDouble()));
    }

    return MemoryChunk(
      id: json['id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      timestamp: json['timestamp'] != null
          ? DateTime.tryParse(json['timestamp'].toString()) ?? DateTime.now()
          : DateTime.now(),
      text: json['text']?.toString() ?? '',
      people: json['people'] != null
          ? List<String>.from((json['people'] as List).map((e) => e.toString()))
          : [],
      location: locationMap,
      tags: json['tags'] != null
          ? List<String>.from((json['tags'] as List).map((e) => e.toString()))
          : [],
      memoryType: json['memory_type']?.toString() ?? 'episodic',
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'user_id': userId,
      'timestamp': timestamp.toIso8601String(),
      'text': text,
      'people': people,
      'location': location,
      'tags': tags,
      'memory_type': memoryType,
    };
  }

  String get formattedTime {
    final hour = timestamp.hour;
    final minute = timestamp.minute.toString().padLeft(2, '0');
    final period = hour >= 12 ? 'PM' : 'AM';
    final displayHour = hour > 12 ? hour - 12 : (hour == 0 ? 12 : hour);
    return '$displayHour:$minute $period';
  }

  String get formattedDate {
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[timestamp.month - 1]} ${timestamp.day}, ${timestamp.year}';
  }

  Color get typeColor {
    switch (memoryType) {
      case 'conversation':
        return const Color(0xFF1565C0);
      case 'activity':
        return const Color(0xFF2E7D32);
      case 'medication':
        return const Color(0xFF6A1B9A);
      case 'meal':
        return const Color(0xFFE65100);
      default:
        return const Color(0xFF37474F);
    }
  }

  IconData get typeIcon {
    switch (memoryType) {
      case 'conversation':
        return Icons.chat_bubble_outline;
      case 'activity':
        return Icons.directions_walk;
      case 'medication':
        return Icons.medication;
      case 'meal':
        return Icons.restaurant;
      default:
        return Icons.memory;
    }
  }
}
