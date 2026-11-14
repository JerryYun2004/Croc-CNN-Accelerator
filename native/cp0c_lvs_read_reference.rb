# Read the complete reference with the same pinned CustomReader used by LVS.
# This is a plain strmrun Ruby program; it does not load or extract chip GDS.
require 'json'
require 'logger'

$cp0_logger = Logger.new($stdout)
def logger; $cp0_logger; end
def dbu; 0.001; end
SERIES_RES = true
PARALLEL_RES = true

%w[globals custom_reader custom_devices custom_combiner].each do |name|
  load File.join($reader_dir, name + '.lvs')
end

expected = JSON.parse(File.read($expectations))
nl = RBA::Netlist.new
nl.read($schematic, RBA::NetlistSpiceReader.new(CustomReader.new))
circuits = {}
models = Hash.new(0)
nl.each_circuit { |c| circuits[c.name.upcase] = c }
raise 'Reader changed circuit inventory or left undefined models' unless circuits.keys.sort == expected['circuits'].keys.sort

inventory = {}
expected['circuits'].each do |name, want|
  c = circuits.fetch(name)
  pins, devices, subcircuits = [], [], []
  c.each_pin { |p| pins << p.name.upcase }
  c.each_device { |d| devices << d; models[d.device_class.name] += 1 }
  c.each_subcircuit { |s| subcircuits << s }
  got = { 'pins' => pins, 'devices' => devices.size, 'subcircuits' => subcircuits.size }
  raise "Reader changed pins or card counts in #{name}" unless pins == want['pins'].map(&:upcase) && devices.size == want['devices'] && subcircuits.size == want['subcircuits']
  inventory[name] = got
end

checks = []
expected['special_devices'].each do |want|
  c = circuits.fetch(want['circuit'])
  found = []
  c.each_device { |d| found << d if d.name.upcase == want['name'] }
  raise "Missing/duplicate translated device #{want['name']}" unless found.size == 1
  d = found.first
  cls = d.device_class
  raise 'Translated device has wrong class' unless cls.name.downcase == want['model']
  terminals = cls.terminal_definitions.to_h { |t| [t.name.downcase, t.id] }
  raise 'Translated device lost a terminal' unless terminals.keys.sort == want['nets'].keys.map(&:downcase).sort
  want['nets'].each do |terminal, net|
    actual = d.net_for_terminal(terminals.fetch(terminal.downcase))
    raise "Translated device changed #{terminal} connection" unless actual && actual.name.upcase == net.upcase
  end
  params = cls.parameter_definitions.to_h { |p| [p.name.downcase, p.id] }
  want['parameters'].each do |parameter, value|
    actual = d.parameter(params.fetch(parameter.downcase))
    raise "Translated device changed #{parameter}" unless (actual-value).abs <= 1e-9 * [1.0, value.abs].max
  end
  checks << {circuit: want['circuit'], device: want['name'], model: cls.name, terminals_and_parameters: 'PASS'}
end

File.write($report, JSON.pretty_generate({status: 'LVS_REFERENCE_READ_PASS',
  circuits: inventory, models_per_definition: models, translated_device_checks: checks,
  devices_per_definition: inventory.values.sum { |c| c['devices'] },
  subcircuits_per_definition: inventory.values.sum { |c| c['subcircuits'] },
  unresolved_subcircuits: [], circuit_blackboxes: []}) + "\n")
puts 'LVS_REFERENCE_READ_PASS: complete reference read; device counts, terminals and geometry verified.'
