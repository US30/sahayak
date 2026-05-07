import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../models/conversation_message.dart';

const _uuid = Uuid();

class ConversationNotifier extends StateNotifier<List<ConversationMessage>> {
  ConversationNotifier() : super([]);

  void addUserMessage(String text) {
    state = [
      ...state,
      ConversationMessage(
        id: _uuid.v4(),
        text: text,
        isUser: true,
        timestamp: DateTime.now(),
      ),
    ];
  }

  void addSahayakMessage(String text) {
    state = [
      ...state,
      ConversationMessage(
        id: _uuid.v4(),
        text: text,
        isUser: false,
        timestamp: DateTime.now(),
      ),
    ];
  }

  void clear() {
    state = [];
  }
}

final conversationProvider =
    StateNotifierProvider<ConversationNotifier, List<ConversationMessage>>(
  (ref) => ConversationNotifier(),
);
