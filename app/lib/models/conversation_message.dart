class ConversationMessage {
  final String id;
  final String text;
  final bool isUser;
  final DateTime timestamp;

  const ConversationMessage({
    required this.id,
    required this.text,
    required this.isUser,
    required this.timestamp,
  });

  String get formattedTime {
    final hour = timestamp.hour;
    final minute = timestamp.minute.toString().padLeft(2, '0');
    final period = hour >= 12 ? 'PM' : 'AM';
    final displayHour = hour > 12 ? hour - 12 : (hour == 0 ? 12 : hour);
    return '$displayHour:$minute $period';
  }
}
