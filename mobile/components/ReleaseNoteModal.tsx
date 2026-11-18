import { Modal, Pressable, ScrollView, StyleSheet, View } from "react-native";

import { Text } from "@/components/AppText";
import { theme } from "@/constants/theme";
import { formatReleaseNoteDate, type ReleaseNote } from "@/lib/releaseNote";

type ReleaseNoteModalProps = {
  note: ReleaseNote;
  onDismiss: () => void;
};

/** One-time What's new card. React Native Modal only — safe for an OTA update. */
export function ReleaseNoteModal({ note, onDismiss }: ReleaseNoteModalProps) {
  return (
    <Modal visible transparent animationType="fade" statusBarTranslucent onRequestClose={onDismiss}>
      <View style={styles.backdrop}>
        <View style={styles.card} accessibilityViewIsModal>
          <Text style={styles.kicker}>What's new</Text>
          <Text style={styles.title}>{note.title}</Text>
          {note.date ? <Text style={styles.date}>{formatReleaseNoteDate(note.date)}</Text> : null}
          <ScrollView style={styles.list} contentContainerStyle={styles.listContent}>
            {note.items.map((item, index) => (
              <View key={`${index}-${item}`} style={styles.row}>
                <View style={styles.dot} />
                <Text style={styles.item}>{item}</Text>
              </View>
            ))}
          </ScrollView>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Got it"
            onPress={onDismiss}
            style={styles.button}
          >
            <Text style={styles.buttonText}>Got it</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
    backgroundColor: theme.overlayHeavy
  },
  card: {
    backgroundColor: theme.surface,
    borderColor: theme.border,
    borderRadius: 16,
    borderWidth: 1,
    maxHeight: "86%",
    padding: 20
  },
  kicker: {
    color: theme.primary,
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase"
  },
  title: {
    color: theme.text,
    fontSize: 22,
    fontWeight: "700",
    marginTop: 4
  },
  date: {
    color: theme.textMuted,
    fontSize: 13,
    marginTop: 4
  },
  list: {
    marginTop: 14
  },
  listContent: {
    gap: 12,
    paddingBottom: 4
  },
  row: {
    flexDirection: "row",
    gap: 10
  },
  dot: {
    backgroundColor: theme.primary,
    borderRadius: 4,
    height: 7,
    marginTop: 7,
    width: 7
  },
  item: {
    color: theme.text,
    flex: 1,
    fontSize: 16,
    lineHeight: 22
  },
  button: {
    alignItems: "center",
    backgroundColor: theme.primary,
    borderRadius: 10,
    marginTop: 16,
    paddingVertical: 14
  },
  buttonText: {
    color: theme.onPrimary,
    fontSize: 16,
    fontWeight: "700"
  }
});
