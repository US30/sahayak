class Person {
  final String id;
  final String userId;
  final String name;
  final String relationship;
  final DateTime? lastSeen;
  final int interactionCount;
  final bool confirmed;

  const Person({
    required this.id,
    required this.userId,
    required this.name,
    required this.relationship,
    this.lastSeen,
    required this.interactionCount,
    required this.confirmed,
  });

  factory Person.fromJson(Map<String, dynamic> json) {
    return Person(
      id: json['id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      relationship: json['relationship']?.toString() ?? '',
      lastSeen: json['last_seen'] != null
          ? DateTime.tryParse(json['last_seen'].toString())
          : null,
      interactionCount: (json['interaction_count'] as num?)?.toInt() ?? 0,
      confirmed: json['confirmed'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'user_id': userId,
      'name': name,
      'relationship': relationship,
      'last_seen': lastSeen?.toIso8601String(),
      'interaction_count': interactionCount,
      'confirmed': confirmed,
    };
  }

  String get initials {
    final parts = name.trim().split(RegExp(r'\s+'));
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts[0][0].toUpperCase();
    return '${parts[0][0]}${parts[parts.length - 1][0]}'.toUpperCase();
  }

  String get lastSeenText {
    if (lastSeen == null) return 'Never seen';
    final now = DateTime.now();
    final diff = now.difference(lastSeen!);
    if (diff.inDays == 0) return 'Today';
    if (diff.inDays == 1) return 'Yesterday';
    if (diff.inDays < 7) return '${diff.inDays} days ago';
    if (diff.inDays < 30) return '${(diff.inDays / 7).floor()} weeks ago';
    return '${(diff.inDays / 30).floor()} months ago';
  }

  Person copyWith({
    String? id,
    String? userId,
    String? name,
    String? relationship,
    DateTime? lastSeen,
    int? interactionCount,
    bool? confirmed,
  }) {
    return Person(
      id: id ?? this.id,
      userId: userId ?? this.userId,
      name: name ?? this.name,
      relationship: relationship ?? this.relationship,
      lastSeen: lastSeen ?? this.lastSeen,
      interactionCount: interactionCount ?? this.interactionCount,
      confirmed: confirmed ?? this.confirmed,
    );
  }
}
