package io.agentic.shortener.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.security.SecureRandom;
import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;

class CodeGeneratorTest {

    private final CodeGenerator generator = new CodeGenerator(new SecureRandom());

    @Test
    void generatesBase62CodesOfRequestedLength() {
        String code = generator.generate(7);
        assertThat(code).hasSize(7).matches("[0-9A-Za-z]{7}");
    }

    @Test
    void codesAreEffectivelyUnique() {
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < 10_000; i++) {
            seen.add(generator.generate(7));
        }
        assertThat(seen).hasSize(10_000);
    }

    @Test
    void rejectsNonPositiveLength() {
        assertThatThrownBy(() -> generator.generate(0)).isInstanceOf(IllegalArgumentException.class);
    }
}
