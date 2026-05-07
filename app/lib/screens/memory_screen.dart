import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import '../models/memory_chunk.dart';
import '../services/api_service.dart';
import '../providers/user_provider.dart';

enum _FilterType { today, week, all }

class MemoryScreen extends ConsumerStatefulWidget {
  const MemoryScreen({super.key});

  @override
  ConsumerState<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends ConsumerState<MemoryScreen> {
  List<MemoryChunk> _memories = [];
  List<MemoryChunk> _filteredMemories = [];
  bool _isLoading = true;
  bool _isSearching = false;
  _FilterType _filter = _FilterType.today;
  String _searchQuery = '';
  final TextEditingController _searchController = TextEditingController();
  String? _expandedId;

  @override
  void initState() {
    super.initState();
    _loadMemories();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadMemories({int hours = 24}) async {
    setState(() => _isLoading = true);
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final memories = await apiService.getRecentMemories(userId, hours: hours);
      setState(() {
        _memories = memories;
        _applyFilter();
      });
    } catch (e) {
      _showError('Failed to load memories: $e');
    } finally {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _searchMemories(String query) async {
    if (query.isEmpty) {
      setState(() {
        _searchQuery = '';
        _applyFilter();
      });
      return;
    }
    setState(() {
      _isSearching = true;
      _searchQuery = query;
    });
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final results = await apiService.queryMemories(userId, query);
      setState(() => _filteredMemories = results);
    } catch (e) {
      // Fall back to client-side filter
      setState(() {
        _filteredMemories = _memories
            .where((m) =>
                m.text.toLowerCase().contains(query.toLowerCase()) ||
                m.tags.any(
                  (t) => t.toLowerCase().contains(query.toLowerCase()),
                ) ||
                m.people.any(
                  (p) => p.toLowerCase().contains(query.toLowerCase()),
                ))
            .toList();
      });
    } finally {
      setState(() => _isSearching = false);
    }
  }

  void _applyFilter() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final weekAgo = today.subtract(const Duration(days: 7));

    switch (_filter) {
      case _FilterType.today:
        _filteredMemories = _memories
            .where((m) => m.timestamp.isAfter(today))
            .toList();
        break;
      case _FilterType.week:
        _filteredMemories = _memories
            .where((m) => m.timestamp.isAfter(weekAgo))
            .toList();
        break;
      case _FilterType.all:
        _filteredMemories = List.from(_memories);
        break;
    }
    // Sort descending
    _filteredMemories.sort((a, b) => b.timestamp.compareTo(a.timestamp));
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFFD32F2F),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Memories'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/'),
        ),
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          _buildFilterChips(),
          Expanded(
            child: _isLoading
                ? _buildShimmerList()
                : RefreshIndicator(
                    onRefresh: () => _loadMemories(
                      hours: _filter == _FilterType.today
                          ? 24
                          : _filter == _FilterType.week
                              ? 168
                              : 720,
                    ),
                    color: const Color(0xFFE65100),
                    child: _filteredMemories.isEmpty
                        ? _buildEmptyState()
                        : _buildMemoryList(),
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildSearchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: TextField(
        controller: _searchController,
        onChanged: (val) => _searchMemories(val),
        style: GoogleFonts.notoSans(fontSize: 18),
        decoration: InputDecoration(
          hintText: 'Search memories…',
          prefixIcon: _isSearching
              ? const Padding(
                  padding: EdgeInsets.all(12),
                  child: SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Color(0xFFE65100),
                    ),
                  ),
                )
              : const Icon(Icons.search, size: 26),
          suffixIcon: _searchController.text.isNotEmpty
              ? IconButton(
                  icon: const Icon(Icons.clear),
                  onPressed: () {
                    _searchController.clear();
                    _searchMemories('');
                  },
                )
              : null,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: const BorderSide(
              color: Color(0xFFE65100),
              width: 2,
            ),
          ),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 14,
          ),
        ),
      ),
    );
  }

  Widget _buildFilterChips() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          _FilterChip(
            label: 'Today',
            selected: _filter == _FilterType.today,
            onTap: () {
              setState(() => _filter = _FilterType.today);
              _applyFilter();
            },
          ),
          const SizedBox(width: 10),
          _FilterChip(
            label: 'This Week',
            selected: _filter == _FilterType.week,
            onTap: () {
              setState(() => _filter = _FilterType.week);
              _loadMemories(hours: 168);
            },
          ),
          const SizedBox(width: 10),
          _FilterChip(
            label: 'All',
            selected: _filter == _FilterType.all,
            onTap: () {
              setState(() => _filter = _FilterType.all);
              _loadMemories(hours: 720);
            },
          ),
        ],
      ),
    );
  }

  Widget _buildMemoryList() {
    // Group by date
    final grouped = <String, List<MemoryChunk>>{};
    for (final m in _filteredMemories) {
      final key = m.formattedDate;
      grouped.putIfAbsent(key, () => []).add(m);
    }

    final keys = grouped.keys.toList();
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: keys.length,
      itemBuilder: (context, i) {
        final dateKey = keys[i];
        final dayMemories = grouped[dateKey]!;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildDateHeader(dateKey),
            ...dayMemories.map((m) => _buildMemoryCard(m)),
            const SizedBox(height: 8),
          ],
        );
      },
    );
  }

  Widget _buildDateHeader(String date) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10, top: 8),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
            decoration: BoxDecoration(
              color: const Color(0xFFE65100),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Text(
              date,
              style: GoogleFonts.notoSans(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: Colors.white,
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(child: Divider(color: Colors.grey[300])),
        ],
      ),
    );
  }

  Widget _buildMemoryCard(MemoryChunk memory) {
    final isExpanded = _expandedId == memory.id;
    return GestureDetector(
      onTap: () {
        setState(() {
          _expandedId = isExpanded ? null : memory.id;
        });
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: isExpanded
                ? const Color(0xFFE65100)
                : Colors.grey.withOpacity(0.2),
            width: isExpanded ? 2 : 1,
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.05),
              blurRadius: 6,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header row
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: memory.typeColor.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Icon(
                      memory.typeIcon,
                      color: memory.typeColor,
                      size: 22,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          memory.memoryType.toUpperCase(),
                          style: GoogleFonts.notoSans(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                            color: memory.typeColor,
                            letterSpacing: 0.5,
                          ),
                        ),
                        Text(
                          memory.formattedTime,
                          style: GoogleFonts.notoSans(
                            fontSize: 14,
                            color: Colors.grey,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Icon(
                    isExpanded
                        ? Icons.keyboard_arrow_up
                        : Icons.keyboard_arrow_down,
                    color: Colors.grey,
                  ),
                ],
              ),
              const SizedBox(height: 10),
              // Text
              Text(
                memory.text,
                style: GoogleFonts.notoSans(
                  fontSize: 18,
                  color: Colors.black87,
                  height: 1.5,
                ),
                maxLines: isExpanded ? null : 2,
                overflow: isExpanded ? null : TextOverflow.ellipsis,
              ),
              if (memory.people.isNotEmpty) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: memory.people
                      .map(
                        (p) => _PersonChip(name: p),
                      )
                      .toList(),
                ),
              ],
              if (isExpanded) ...[
                if (memory.tags.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    children: memory.tags
                        .map(
                          (t) => Chip(
                            label: Text(
                              '#$t',
                              style: GoogleFonts.notoSans(
                                fontSize: 14,
                                color: Colors.grey[700],
                              ),
                            ),
                            backgroundColor: Colors.grey[100],
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 2),
                          ),
                        )
                        .toList(),
                  ),
                ],
                if (memory.location != null) ...[
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: const Color(0xFFE8F5E9),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(
                          Icons.location_on,
                          color: Color(0xFF2E7D32),
                          size: 20,
                        ),
                        const SizedBox(width: 6),
                        Text(
                          'Lat: ${memory.location!['lat']?.toStringAsFixed(4)}, '
                          'Lng: ${memory.location!['lng']?.toStringAsFixed(4)}',
                          style: GoogleFonts.notoSans(
                            fontSize: 15,
                            color: const Color(0xFF2E7D32),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.memory, size: 80, color: Colors.grey[300]),
          const SizedBox(height: 16),
          Text(
            'No memories found',
            style: GoogleFonts.notoSans(fontSize: 22, color: Colors.grey),
          ),
          const SizedBox(height: 8),
          Text(
            'Pull down to refresh',
            style: GoogleFonts.notoSans(
                fontSize: 17, color: Colors.grey[400]),
          ),
        ],
      ),
    );
  }

  Widget _buildShimmerList() {
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: 5,
      itemBuilder: (context, _) => _buildShimmerCard(),
    );
  }

  Widget _buildShimmerCard() {
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      height: 100,
      decoration: BoxDecoration(
        color: Colors.grey[200],
        borderRadius: BorderRadius.circular(16),
      ),
    );
  }
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _FilterChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? const Color(0xFFE65100) : Colors.white,
          borderRadius: BorderRadius.circular(24),
          border: Border.all(
            color: selected ? const Color(0xFFE65100) : Colors.grey[300]!,
          ),
          boxShadow: selected
              ? [
                  BoxShadow(
                    color: const Color(0xFFE65100).withOpacity(0.3),
                    blurRadius: 6,
                  )
                ]
              : null,
        ),
        child: Text(
          label,
          style: GoogleFonts.notoSans(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: selected ? Colors.white : Colors.black54,
          ),
        ),
      ),
    );
  }
}

class _PersonChip extends StatelessWidget {
  final String name;

  const _PersonChip({required this.name});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF3E0),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xFFFFB300)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.person, size: 16, color: Color(0xFFE65100)),
          const SizedBox(width: 4),
          Text(
            name,
            style: GoogleFonts.notoSans(
              fontSize: 15,
              color: const Color(0xFFE65100),
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
