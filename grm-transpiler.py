import sys
import re
import os
import textwrap

def replace_internal_calls(code, struct_name):
    # Matches Struct.Method(
    pattern = re.compile(fr'\b{struct_name}\.([a-zA-Z0-9_]+)\s*\(')
    idx = 0
    while True:
        match = pattern.search(code, idx)
        if not match: break

        method_name = match.group(1)
        start_args = match.end()
        brace_count = 1
        end_args = -1

        for i in range(start_args, len(code)):
            if code[i] == '(': brace_count += 1
            elif code[i] == ')':
                brace_count -= 1
                if brace_count == 0:
                    end_args = i
                    break

        if end_args != -1:
            args = code[start_args:end_args].strip()
            # Handle nested calls inside arguments first
            args = replace_internal_calls(args, struct_name)

            replacement = f"{struct_name}_{method_name}(self"
            if args: replacement += f", {args}"
            replacement += ")"

            code = code[:match.start()] + replacement + code[end_args+1:]
            # Move index past the replacement to avoid infinite recursion
            idx = match.start() + len(replacement)
        else:
            idx = match.end()
    return code

def replace_external_calls(code, var_types):
    # Complex pattern to handle paths like player.pos.move(
    pattern = re.compile(r'\b((?:[a-zA-Z0-9_]+(?:\[[^\]]*\])*(?:\.|->))*)([a-zA-Z0-9_]+)((?:\[[^\]]*\])*)(\.|->)([a-zA-Z0-9_]+)\s*\(')
    idx = 0
    while True:
        match = pattern.search(code, idx)
        if not match: break
            
        prefix, base_var, indices, operator, method_name = match.groups()
        
        if base_var not in var_types:
            idx = match.end()
            continue
            
        start_args = match.end()
        brace_count = 1
        end_args = -1
        
        for i in range(start_args, len(code)):
            if code[i] == '(': brace_count += 1
            elif code[i] == ')':
                brace_count -= 1
                if brace_count == 0:
                    end_args = i
                    break
                    
        if end_args != -1:
            args = code[start_args:end_args].strip()
            args = replace_external_calls(args, var_types)
            
            struct_name = var_types[base_var]
            full_var = prefix + base_var + indices 
            
            self_arg = f"&{full_var}" if operator == '.' else full_var
            replacement = f"{struct_name}_{method_name}({self_arg}"
            if args: replacement += f", {args}"
            replacement += ")"
            
            code = code[:match.start()] + replacement + code[end_args+1:]
            idx = match.start() + len(replacement)
        else:
            idx = match.end()
    return code


def compile_grm(input_file, output_file):
    print(f"[INFO] Compiling {input_file}...")
    with open(input_file, 'r') as f:
        code = f.read()

    # 1. First Pass: Map all structs and their fields
    structs = {}
    struct_pattern = re.compile(r'typedef\s+struct(?:\s+[a-zA-Z0-9_]+)?\s*\{([^}]*)\}\s*([a-zA-Z0-9_]+)\s*;')

    for match in struct_pattern.finditer(code):
        body, name = match.group(1), match.group(2)
        members = re.findall(r'\b([a-zA-Z0-9_]+)\s*(?:\[.*?\])?\s*;', body)
        structs[name] = members

    # 2. Second Pass: Extract and translate impl blocks into a separate buffer
    # This prevents the script from finding its own output in the original code
    new_code = ""
    last_idx = 0
    impl_pattern = re.compile(r'impl\s+([a-zA-Z0-9_]+)\s*\{')

    while True:
        match = impl_pattern.search(code, last_idx)
        if not match:
            new_code += code[last_idx:]
            break
            
        # Append anything before the impl block
        new_code += code[last_idx:match.start()]
        
        struct_name = match.group(1)
        print(f"  -> Processing impl for: {struct_name}")
        
        # Brace counting to find end of impl block
        start_impl = match.end() - 1
        brace_count = 0
        end_impl = -1
        for i in range(start_impl, len(code)):
            if code[i] == '{': brace_count += 1
            elif code[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_impl = i
                    break
                    
        impl_body = code[start_impl+1:end_impl]
        
        # Process methods within this block
        translated_impl = ""
        f_idx = 0
        func_pattern = re.compile(r'([a-zA-Z0-9_]+\s*\*?)\s+([a-zA-Z0-9_]+)\s*\((.*?)\)\s*\{')

        while True:
            f_match = func_pattern.search(impl_body, f_idx)
            if not f_match: break

            ret_type, func_name, args = f_match.group(1).strip(), f_match.group(2).strip(), f_match.group(3).strip()
            print(f"    - Translating: {func_name}()")
            
            f_start = f_match.end() - 1
            f_brace_count = 0
            f_end = -1
            for i in range(f_start, len(impl_body)):
                if impl_body[i] == '{': f_brace_count += 1
                elif impl_body[i] == '}':
                    f_brace_count -= 1
                    if f_brace_count == 0:
                        f_end = i
                        break

            raw_body = impl_body[f_start+1:f_end]
            clean_body = replace_internal_calls(raw_body, struct_name)

            # Replace StructName.Field -> self->Field
            if struct_name in structs:
                for field in structs[struct_name]:
                    clean_body = re.sub(fr'\b{struct_name}\.{field}\b', f'self->{field}', clean_body)

            indented_body = textwrap.indent(textwrap.dedent(clean_body).strip(), '    ')
            
            c_args = f"{struct_name}* self"
            if args: c_args += f", {args}"

            translated_impl += f"\n{ret_type} {struct_name}_{func_name}({c_args}) {{\n{indented_body}\n}}\n"
            f_idx = f_end + 1

        new_code += translated_impl
        last_idx = end_impl + 1

    code = new_code

    # 3. Third Pass: Identify variables of custom struct types
    var_types = {}
    for struct_name in structs.keys():
        var_pattern = re.compile(fr'\b{struct_name}\s*\*+\s*([a-zA-Z0-9_]+)\b|\b{struct_name}\s+([a-zA-Z0-9_]+)\b')
        for m in var_pattern.finditer(code):
            v_name = m.group(1) or m.group(2)
            var_types[v_name] = struct_name

    # 4. Final Pass: Replace external calls (e.g., game.update())
    print("[INFO] Finalizing external method calls...")
    code = replace_external_calls(code, var_types)

    with open(output_file, 'w') as f:
        f.write(code.strip() + '\n')
    print(f"[SUCCESS] File saved as {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    compile_grm(sys.argv[1], os.path.splitext(sys.argv[1])[0] + ".c")