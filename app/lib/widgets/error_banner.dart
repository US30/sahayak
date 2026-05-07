import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Dismissible error banner for inline error display.
class ErrorBanner extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;
  final VoidCallback? onDismiss;

  const ErrorBanner({
    super.key,
    required this.message,
    this.onRetry,
    this.onDismiss,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: const Color(0xFFFFEBEE),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: const Color(0xFFD32F2F).withOpacity(0.5),
        ),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.error_outline,
            color: Color(0xFFD32F2F),
            size: 26,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              message,
              style: GoogleFonts.notoSans(
                fontSize: 16,
                color: const Color(0xFFB71C1C),
              ),
            ),
          ),
          if (onRetry != null)
            TextButton(
              onPressed: onRetry,
              child: Text(
                'Retry',
                style: GoogleFonts.notoSans(
                  fontSize: 16,
                  color: const Color(0xFFD32F2F),
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          if (onDismiss != null)
            IconButton(
              icon: const Icon(
                Icons.close,
                color: Color(0xFFD32F2F),
                size: 22,
              ),
              onPressed: onDismiss,
            ),
        ],
      ),
    );
  }
}
