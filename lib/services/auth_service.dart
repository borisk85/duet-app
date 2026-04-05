import 'package:firebase_auth/firebase_auth.dart';
import 'package:google_sign_in/google_sign_in.dart';

class AuthService {
  static final _auth = FirebaseAuth.instance;
  static final _googleSignIn = GoogleSignIn();

  static User? get currentUser => _auth.currentUser;
  static Stream<User?> get authStateChanges => _auth.authStateChanges();

  static Future<String?> getIdToken() async {
    return await _auth.currentUser?.getIdToken();
  }

  static Future<UserCredential?> signInWithGoogle() async {
    final googleUser = await _googleSignIn.signIn();
    if (googleUser == null) return null; // пользователь отменил

    final googleAuth = await googleUser.authentication;
    final credential = GoogleAuthProvider.credential(
      accessToken: googleAuth.accessToken,
      idToken: googleAuth.idToken,
    );
    return await _auth.signInWithCredential(credential);
  }

  static Future<UserCredential> signInAnonymously() async {
    return await _auth.signInAnonymously();
  }

  static Future<void> signOut() async {
    await _googleSignIn.signOut();
    await _auth.signOut();
  }

  static bool get isAnonymous => _auth.currentUser?.isAnonymous ?? true;

  static String get displayName {
    final user = _auth.currentUser;
    if (user == null || user.isAnonymous) return 'Гость';
    return user.displayName ?? user.email ?? 'Пользователь';
  }
}
