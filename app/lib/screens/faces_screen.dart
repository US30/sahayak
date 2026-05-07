import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:image_picker/image_picker.dart';
import '../models/person.dart';
import '../services/api_service.dart';
import '../providers/user_provider.dart';

class FacesScreen extends ConsumerStatefulWidget {
  const FacesScreen({super.key});

  @override
  ConsumerState<FacesScreen> createState() => _FacesScreenState();
}

class _FacesScreenState extends ConsumerState<FacesScreen> {
  List<Person> _persons = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadPersons();
  }

  Future<void> _loadPersons() async {
    setState(() => _isLoading = true);
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final persons = await apiService.getPersons(userId);
      setState(() => _persons = persons);
    } catch (e) {
      _showError('Failed to load contacts: $e');
    } finally {
      setState(() => _isLoading = false);
    }
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

  void _showSuccess(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: const Color(0xFF2E7D32),
      ),
    );
  }

  Future<void> _showAddPersonDialog() async {
    final nameController = TextEditingController();
    String selectedRelationship = 'Family';
    Uint8List? imageBytes;
    final formKey = GlobalKey<FormState>();

    const relationships = [
      'Family',
      'Spouse',
      'Child',
      'Sibling',
      'Parent',
      'Friend',
      'Caregiver',
      'Doctor',
      'Neighbour',
      'Other',
    ];

    await showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) {
          Future<void> pickImage(ImageSource source) async {
            try {
              final picker = ImagePicker();
              final picked = await picker.pickImage(
                source: source,
                maxWidth: 800,
                maxHeight: 800,
                imageQuality: 85,
              );
              if (picked != null) {
                final bytes = await picked.readAsBytes();
                setDialogState(() => imageBytes = bytes);
              }
            } catch (e) {
              _showError('Could not pick image: $e');
            }
          }

          return AlertDialog(
            title: const Text('Add Person'),
            contentPadding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
            content: SingleChildScrollView(
              child: Form(
                key: formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Photo section
                    GestureDetector(
                      onTap: () => showModalBottomSheet(
                        context: ctx,
                        builder: (bctx) => SafeArea(
                          child: Wrap(
                            children: [
                              ListTile(
                                leading: const Icon(Icons.camera_alt),
                                title: Text(
                                  'Camera',
                                  style: GoogleFonts.notoSans(fontSize: 18),
                                ),
                                onTap: () {
                                  Navigator.pop(bctx);
                                  pickImage(ImageSource.camera);
                                },
                              ),
                              ListTile(
                                leading: const Icon(Icons.photo_library),
                                title: Text(
                                  'Gallery',
                                  style: GoogleFonts.notoSans(fontSize: 18),
                                ),
                                onTap: () {
                                  Navigator.pop(bctx);
                                  pickImage(ImageSource.gallery);
                                },
                              ),
                            ],
                          ),
                        ),
                      ),
                      child: Container(
                        width: 110,
                        height: 110,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: Colors.grey[200],
                          border: Border.all(
                            color: const Color(0xFFE65100),
                            width: 2,
                          ),
                          image: imageBytes != null
                              ? DecorationImage(
                                  image: MemoryImage(imageBytes!),
                                  fit: BoxFit.cover,
                                )
                              : null,
                        ),
                        child: imageBytes == null
                            ? Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  const Icon(
                                    Icons.camera_alt,
                                    color: Color(0xFFE65100),
                                    size: 32,
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    'Add Photo',
                                    style: GoogleFonts.notoSans(
                                      fontSize: 13,
                                      color: const Color(0xFFE65100),
                                    ),
                                  ),
                                ],
                              )
                            : null,
                      ),
                    ),
                    const SizedBox(height: 20),
                    // Name field
                    TextFormField(
                      controller: nameController,
                      style: GoogleFonts.notoSans(fontSize: 18),
                      decoration: const InputDecoration(
                        labelText: 'Name',
                        prefixIcon: Icon(Icons.person),
                      ),
                      validator: (v) =>
                          v == null || v.trim().isEmpty ? 'Name is required' : null,
                    ),
                    const SizedBox(height: 16),
                    // Relationship dropdown
                    DropdownButtonFormField<String>(
                      value: selectedRelationship,
                      decoration: const InputDecoration(
                        labelText: 'Relationship',
                        prefixIcon: Icon(Icons.people),
                      ),
                      style: GoogleFonts.notoSans(
                        fontSize: 18,
                        color: Colors.black87,
                      ),
                      items: relationships
                          .map(
                            (r) => DropdownMenuItem(
                              value: r,
                              child: Text(r),
                            ),
                          )
                          .toList(),
                      onChanged: (v) {
                        if (v != null) {
                          setDialogState(() => selectedRelationship = v);
                        }
                      },
                    ),
                    const SizedBox(height: 8),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancel'),
              ),
              ElevatedButton(
                onPressed: () async {
                  if (!formKey.currentState!.validate()) return;
                  if (imageBytes == null) {
                    _showError('Please add a photo for face recognition.');
                    return;
                  }
                  Navigator.pop(ctx);
                  await _registerPerson(
                    nameController.text.trim(),
                    selectedRelationship,
                    imageBytes!,
                  );
                },
                child: const Text('Register'),
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _registerPerson(
    String name,
    String relationship,
    Uint8List imageBytes,
  ) async {
    try {
      final userId = ref.read(userIdProvider);
      final apiService = ref.read(apiServiceProvider);
      final person = await apiService.registerFace(
        name,
        relationship,
        userId,
        imageBytes,
      );
      setState(() => _persons.add(person));
      _showSuccess('$name registered successfully!');
    } catch (e) {
      _showError('Failed to register: $e');
    }
  }

  Future<void> _showPersonOptions(Person person) async {
    final result = await showModalBottomSheet<String>(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.grey[300],
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Text(
                person.name,
                style: GoogleFonts.notoSans(
                  fontSize: 22,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              if (!person.confirmed)
                ListTile(
                  leading: const Icon(
                    Icons.check_circle,
                    color: Color(0xFF2E7D32),
                    size: 28,
                  ),
                  title: Text(
                    'Confirm Person',
                    style: GoogleFonts.notoSans(fontSize: 18),
                  ),
                  subtitle: Text(
                    'Approve this face registration',
                    style: GoogleFonts.notoSans(fontSize: 15),
                  ),
                  onTap: () => Navigator.pop(ctx, 'confirm'),
                ),
              ListTile(
                leading: const Icon(
                  Icons.delete,
                  color: Color(0xFFD32F2F),
                  size: 28,
                ),
                title: Text(
                  'Remove Person',
                  style: GoogleFonts.notoSans(
                    fontSize: 18,
                    color: const Color(0xFFD32F2F),
                  ),
                ),
                onTap: () => Navigator.pop(ctx, 'delete'),
              ),
              ListTile(
                leading: const Icon(Icons.close, size: 28),
                title: Text(
                  'Cancel',
                  style: GoogleFonts.notoSans(fontSize: 18),
                ),
                onTap: () => Navigator.pop(ctx),
              ),
            ],
          ),
        ),
      ),
    );

    if (result == 'confirm') {
      await _confirmPerson(person);
    } else if (result == 'delete') {
      await _deletePerson(person);
    }
  }

  Future<void> _confirmPerson(Person person) async {
    try {
      final apiService = ref.read(apiServiceProvider);
      await apiService.confirmPerson(person.id);
      setState(() {
        final idx = _persons.indexWhere((p) => p.id == person.id);
        if (idx >= 0) {
          _persons[idx] = person.copyWith(confirmed: true);
        }
      });
      _showSuccess('${person.name} confirmed!');
    } catch (e) {
      _showError('Failed to confirm: $e');
    }
  }

  Future<void> _deletePerson(Person person) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Remove Person'),
        content: Text('Remove ${person.name} from the registry?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFD32F2F),
            ),
            child: const Text('Remove'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;
    try {
      final apiService = ref.read(apiServiceProvider);
      await apiService.deletePerson(person.id);
      setState(() => _persons.removeWhere((p) => p.id == person.id));
      _showSuccess('${person.name} removed.');
    } catch (e) {
      _showError('Failed to remove: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('People'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/'),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadPersons,
            tooltip: 'Refresh',
          ),
        ],
      ),
      body: _isLoading
          ? _buildLoadingGrid()
          : RefreshIndicator(
              onRefresh: _loadPersons,
              color: const Color(0xFFE65100),
              child: _persons.isEmpty
                  ? _buildEmptyState()
                  : _buildPersonGrid(),
            ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _showAddPersonDialog,
        icon: const Icon(Icons.person_add),
        label: Text(
          'Add Person',
          style: GoogleFonts.notoSans(
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }

  Widget _buildPersonGrid() {
    return GridView.builder(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 80),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        childAspectRatio: 0.78,
        crossAxisSpacing: 14,
        mainAxisSpacing: 14,
      ),
      itemCount: _persons.length,
      itemBuilder: (context, index) {
        return _buildPersonCard(_persons[index]);
      },
    );
  }

  Widget _buildPersonCard(Person person) {
    return GestureDetector(
      onLongPress: () => _showPersonOptions(person),
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(18),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.08),
              blurRadius: 8,
              offset: const Offset(0, 3),
            ),
          ],
        ),
        child: Stack(
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  // Avatar
                  CircleAvatar(
                    radius: 44,
                    backgroundColor: const Color(0xFFFFE0B2),
                    child: Text(
                      person.initials,
                      style: GoogleFonts.notoSans(
                        fontSize: 30,
                        fontWeight: FontWeight.w700,
                        color: const Color(0xFFE65100),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    person.name,
                    style: GoogleFonts.notoSans(
                      fontSize: 19,
                      fontWeight: FontWeight.w700,
                      color: Colors.black87,
                    ),
                    textAlign: TextAlign.center,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFFF3E0),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      person.relationship,
                      style: GoogleFonts.notoSans(
                        fontSize: 14,
                        color: const Color(0xFFE65100),
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    person.lastSeenText,
                    style: GoogleFonts.notoSans(
                      fontSize: 13,
                      color: Colors.grey,
                    ),
                    textAlign: TextAlign.center,
                  ),
                  if (person.interactionCount > 0) ...[
                    const SizedBox(height: 4),
                    Text(
                      '${person.interactionCount} interactions',
                      style: GoogleFonts.notoSans(
                        fontSize: 12,
                        color: Colors.grey[400],
                      ),
                    ),
                  ],
                ],
              ),
            ),
            // Unconfirmed badge
            if (!person.confirmed)
              Positioned(
                top: 8,
                right: 8,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFFF57C00),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    'Pending',
                    style: GoogleFonts.notoSans(
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.people_outline, size: 80, color: Colors.grey[300]),
          const SizedBox(height: 16),
          Text(
            'No people registered yet',
            style: GoogleFonts.notoSans(fontSize: 22, color: Colors.grey),
          ),
          const SizedBox(height: 8),
          Text(
            'Tap "Add Person" to register family\nand friends for recognition',
            style: GoogleFonts.notoSans(
              fontSize: 17,
              color: Colors.grey[400],
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  Widget _buildLoadingGrid() {
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        childAspectRatio: 0.78,
        crossAxisSpacing: 14,
        mainAxisSpacing: 14,
      ),
      itemCount: 4,
      itemBuilder: (context, _) => Container(
        decoration: BoxDecoration(
          color: Colors.grey[200],
          borderRadius: BorderRadius.circular(18),
        ),
      ),
    );
  }
}
